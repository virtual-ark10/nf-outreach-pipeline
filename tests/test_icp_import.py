#!/usr/bin/env python3
"""The ICP importer, offline: --limit caps NEW leads, not list positions.

icp_research walks the scored, importable accounts and POSTs the ones the CRM does not
already hold. The daily cron asks for --limit 60, so what that number counts decides
whether the uncontacted pool keeps filling. Capped against list position (the original
`importable[:limit]`), the accounts already in the CRM eat the whole budget and the tail
of the list is never examined on that run or any later one. These cases pin the counting
rule in main() with the scan, the scoring and the CRM stubbed out.
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
    """main() with the scan, the scoring and the CRM stubbed out."""

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
                mock.patch.object(icp, "parse_seeds",
                                  lambda p: [("d.com", "signal_data", True)]), \
                mock.patch.object(icp, "load_spec",
                                  lambda p: {"icp_name": "t",
                                             "bands": {"import": ["strong", "workable"]}}), \
                mock.patch.object(icp, "scan", lambda seeds, refresh: {}), \
                mock.patch.object(icp, "build",
                                  lambda seeds, raw, spec, names=None: list(accounts)), \
                mock.patch.object(icp, "write_artifacts", lambda c, s: None), \
                mock.patch.object(sys, "argv", argv):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = icp.main()
        self.assertEqual(rc, 0)
        return posted, out.getvalue()

    def test_present_accounts_do_not_eat_the_limit(self):
        # The scenario the widened seed list exposed: 5 already present at the head of 70.
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
