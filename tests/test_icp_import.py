#!/usr/bin/env python3
"""The ICP importer and discovery, offline.

Two things are pinned here:

1. --limit caps NEW leads, not list positions. icp_research walks the scored,
   importable accounts and POSTs the ones the CRM does not already hold. Capped
   against list position (the original `importable[:limit]`), the accounts already
   in the CRM eat the whole budget and the tail of the list is never examined on
   that run or any later one. These cases pin the counting rule in main().

2. Discovery from the Diffbot KG is deterministic and safe: the query is built from
   the spec's bands, categories map to segments, candidates are ranked and deduped,
   the free-text summary is never requested, and a bad domain is dropped.
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

NF_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(NF_REPO, "scripts"))

import icp_research as icp   # noqa: E402


SPEC_MIN = {"icp_name": "t", "bands": {"import": ["strong", "workable"]},
            "segments": {"signal_data": 3, "enrichment": 2, "infra": 1},
            "discovery": {"bands": {"employees": [11, 200],
                                    "founded": ["2018-01-01", "2024-12-31"],
                                    "funding_usd": [500000, 30000000]},
                          "geo_countries": ["United States", "Germany"],
                          "segments": {"signal_data": ["Sales Intelligence Software"],
                                       "enrichment": ["Data Quality Tools"],
                                       "infra": ["Data Extraction Software"]},
                          "pool": 250, "min_entity_confidence": 0.6},
            "scoring_weights": {"industry_fit": 15, "size_fit": 15, "geography": 10,
                                "tech_stack_fit": 15, "buyer_role_match": 15,
                                "trigger_event_present": 15, "budget_authority": 10,
                                "timeline": 5}}


def account(name, band="strong", importable=True):
    domain = name.lower().replace(" ", "") + ".com"
    return {
        "company": name, "domain": domain, "segment": "signal_data", "importable": importable,
        "score": 90, "band": band, "people_found": 1, "hq": "Berlin", "industry": "Data",
        "employee_count": "50", "revenue": None,
        "gtm_contacts": [{"full_name": "A B", "title": "Growth Lead", "linkedin": "https://x",
                          "company": name, "domain": domain}],
    }


class ImportLimit(unittest.TestCase):
    """main() with discovery, the scan, the scoring and the CRM stubbed out."""

    def run_import(self, accounts, present=(), limit=60, apply=True):
        posted = []

        def fake_crm(method, path, token, body=None, tries=3):
            if method == "GET":
                return 200, {"leads": [{"company": c} for c in present]}
            posted.append(body["company"])
            return 201, {}

        argv = ["icp_research.py"] + (["--apply"] if apply else []) + ["--limit", str(limit)]
        with mock.patch.object(icp, "crm_call", fake_crm), \
                mock.patch.object(icp, "load_token", lambda: "t"), \
                mock.patch.object(icp, "load_spec", lambda p: SPEC_MIN), \
                mock.patch.object(icp, "discover",
                                  lambda spec, pool, verify=False: ([("d.com", "signal_data", True)],
                                                                    {"d.com": {"company": "D"}})), \
                mock.patch.object(icp, "scan", lambda seeds, refresh: {}), \
                mock.patch.object(icp, "build",
                                  lambda seeds, raw, spec, names=None, firm=None: list(accounts)), \
                mock.patch.object(icp, "write_artifacts", lambda c, s: None), \
                mock.patch.object(sys, "argv", argv):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = icp.main()
        self.assertEqual(rc, 0)
        return posted, out.getvalue()

    def test_present_accounts_do_not_eat_the_limit(self):
        accounts = ([account(f"Present Co {i}") for i in range(1, 6)]
                    + [account(f"Fresh Co {i}") for i in range(1, 66)])
        posted, _ = self.run_import(accounts, present=[f"Present Co {i}" for i in range(1, 6)],
                                    limit=10)
        self.assertEqual(len(posted), 10)
        self.assertEqual(posted[0], "Fresh Co 1")
        self.assertEqual(posted[-1], "Fresh Co 10")

    def test_the_limit_is_reported_when_it_bites(self):
        accounts = [account(f"Fresh Co {i}") for i in range(1, 66)]
        _, out = self.run_import(accounts, limit=10)
        self.assertIn("55 left unexamined", out)

    def test_a_plan_run_counts_the_same_way_but_writes_nothing(self):
        accounts = ([account(f"Present Co {i}") for i in range(1, 6)]
                    + [account(f"Fresh Co {i}") for i in range(1, 66)])
        posted, out = self.run_import(accounts, present=[f"Present Co {i}" for i in range(1, 6)],
                                      limit=10, apply=False)
        self.assertEqual(posted, [])
        self.assertEqual(out.count("would import"), 10)

    def test_a_company_the_crm_already_holds_is_skipped_not_posted(self):
        posted, out = self.run_import([account("Present Co 1"), account("Fresh Co 1")],
                                      present=["Present Co 1"])
        self.assertEqual(posted, ["Fresh Co 1"])
        self.assertIn("already present 1", out)

    def test_the_min_band_floor_still_keeps_marginal_accounts_out(self):
        posted, _ = self.run_import([account("Strong Co", band="strong"),
                                     account("Marginal Co", band="marginal")])
        self.assertEqual(posted, ["Strong Co"])

    def test_discovery_failure_aborts_cleanly(self):
        argv = ["icp_research.py"]
        with mock.patch.object(icp, "load_spec", lambda p: SPEC_MIN), \
                mock.patch.object(icp, "discover", lambda spec, pool, verify=False: ([], {})), \
                mock.patch.object(sys, "argv", argv):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = icp.main()
        self.assertEqual(rc, 1)
        self.assertIn("nothing discovered", out.getvalue())


class Discovery(unittest.TestCase):
    """The Diffbot discovery path, with the DQL call stubbed."""

    def test_query_uses_the_spec_bands(self):
        q = icp.build_discovery_query(SPEC_MIN)
        self.assertIn("type:Organization", q)
        self.assertIn("range:nbEmployees:11-200", q)
        self.assertIn('foundingDate>="2018-01-01"', q)
        self.assertIn('foundingDate<="2024-12-31"', q)
        self.assertIn("totalInvestment.value>=500000", q)
        self.assertIn("totalInvestment.value<=30000000", q)
        self.assertIn('"Sales Intelligence Software"', q)
        self.assertNotIn("summary", q)

    def test_categories_map_to_segments(self):
        self.assertEqual(icp.segment_of([{"name": "Sales Intelligence Software"}], SPEC_MIN),
                         "signal_data")
        self.assertEqual(icp.segment_of([{"name": "Data Quality Tools"}], SPEC_MIN), "enrichment")
        self.assertEqual(icp.segment_of([{"name": "Data Extraction Software"}], SPEC_MIN), "infra")
        # signal_data outranks infra when both match
        self.assertEqual(icp.segment_of([{"name": "Data Extraction Software"},
                                         {"name": "Sales Intelligence Software"}], SPEC_MIN),
                         "signal_data")

    def test_domain_normalization(self):
        self.assertEqual(icp.normalize_domain("https://www.Acme.COM/about"), "acme.com")
        self.assertEqual(icp.normalize_domain("acme.io"), "acme.io")
        self.assertEqual(icp.normalize_domain(""), "")
        self.assertEqual(icp.normalize_domain("localhost"), "")

    def test_discovery_ranks_dedupes_and_never_asks_for_summary(self):
        payload = {"data": [
            {"entity": {"name": "Low Imp", "homepageUri": "low.com", "nbEmployees": 20,
                        "importance": 10, "categories": [{"name": "Data Quality Tools"}]}},
            {"entity": {"name": "High Imp", "homepageUri": "high.com", "nbEmployees": 30,
                        "importance": 90,
                        "categories": [{"name": "Sales Intelligence Software"}],
                        "totalInvestment": {"value": 2000000},
                        "location": {"country": {"name": "Germany"}}}},
            {"entity": {"name": "Dup High", "homepageUri": "HIGH.com", "nbEmployees": 99,
                        "importance": 50, "categories": []}},          # dup domain -> dropped
            {"entity": {"name": "No Domain", "homepageUri": "", "nbEmployees": 40,
                        "importance": 99, "categories": []}},          # no domain -> dropped
            {"entity": {"name": "Bad Domain", "homepageUri": "localhost", "nbEmployees": 40,
                        "importance": 99, "categories": []}},          # invalid -> dropped
        ]}
        seen = {}

        def fake_dql(dql, size, token):
            seen["dql"] = dql
            seen["get"] = icp.DQL_FIELDS
            return payload

        with mock.patch.object(icp, "load_diffbot_token", lambda: "t"), \
                mock.patch.object(icp, "_diffbot_dql", fake_dql):
            seeds, firm = icp.discover(SPEC_MIN, pool=250)

        self.assertEqual([s[0] for s in seeds], ["high.com", "low.com"])   # ranked, deduped
        self.assertEqual(seeds[0][1], "signal_data")
        self.assertEqual(firm["high.com"]["funding"], 2000000)
        self.assertEqual(firm["high.com"]["country"], "Germany")
        self.assertNotIn("summary", seen["get"])                            # rule: never request it

    def test_discovery_returns_empty_on_api_failure(self):
        def boom(dql, size, token):
            raise RuntimeError("HTTP 500")

        with mock.patch.object(icp, "load_diffbot_token", lambda: "t"), \
                mock.patch.object(icp, "_diffbot_dql", boom):
            seeds, firm = icp.discover(SPEC_MIN, pool=10)
        self.assertEqual(seeds, [])
        self.assertEqual(firm, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
