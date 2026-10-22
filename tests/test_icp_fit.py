#!/usr/bin/env python3
"""The ICP fit rule, offline.

No network: the size cache is injected, which is also how the pipeline runs it after a scan.
The fixtures are the real ones from the 2026-10-12 audit — an enterprise brand that scored
MEDIUM in the sponsor export did reach the first-touch queue (Box 59, Atlassian 53), which is
why the rule exists and why "unknown size is kept" matters: dropping an unmeasurable company
would silently shrink the pool to whatever the free lane happens to index.
"""
import json
import os
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

# Keep the module off the real cache/CRM before importing it.
TMP = tempfile.mkdtemp()
os.environ["NF_ICP_SIZE_CACHE"] = os.path.join(TMP, "size-cache.json")
os.environ["NF_CRM_DB"] = os.path.join(TMP, "crm.db")
os.environ["TREG_BIN"] = os.path.join(TMP, "no-such-treg")      # any fetch is a hard fail

import icp_fit  # noqa: E402


class Bands(unittest.TestCase):
    def test_band_max_reads_the_upper_bound(self):
        self.assertEqual(icp_fit.band_max("5000 - 9999"), 9999)
        self.assertEqual(icp_fit.band_max("11 - 50"), 50)
        self.assertEqual(icp_fit.band_max("1 - 10"), 10)
        self.assertEqual(icp_fit.band_max("201 - 500"), 500)
        self.assertEqual(icp_fit.band_max("50"), 50)
        self.assertEqual(icp_fit.band_max("1001+"), 1001)
        self.assertIsNone(icp_fit.band_max(None))
        self.assertIsNone(icp_fit.band_max("n/a"))

    def test_band_min_is_what_the_exclusion_uses(self):
        # The free lane's bands are LinkedIn-style and run high: "100 - 249" must not read as
        # 249, or most of the mid-market pool is thrown out on a source's coarse bucket.
        self.assertEqual(icp_fit.band_min("100 - 249"), 100)
        self.assertEqual(icp_fit.band_min("250 - 499"), 250)
        self.assertEqual(icp_fit.band_min("1 - 10"), 1)
        self.assertEqual(icp_fit.band_min(">10000"), 10000)
        self.assertEqual(icp_fit.band_min("50"), 50)
        self.assertIsNone(icp_fit.band_min(None))

    def test_the_spec_band_is_the_documented_one(self):
        self.assertEqual(icp_fit.size_band(), (11, 200))

    def test_norm_domain_and_subdomains(self):
        self.assertEqual(icp_fit.norm_domain("https://WWW.Box.com/pricing"), "box.com")
        self.assertEqual(icp_fit.norm_domain("press@atlassian.com"), "atlassian.com")


class Verdict(unittest.TestCase):
    def test_in_band_is_a_fit(self):
        fit, reason, _ = icp_fit.verdict("Amberdata", "amberdata.io", employees=80)
        self.assertTrue(fit)
        self.assertEqual(reason, "in-band")

    def test_an_enterprise_brand_is_not(self):
        # Measured 2026-10-12 cases: a company sized over the ceiling is out wherever it came
        # from, whether or not it is on the hand-held list.
        for company, domain, emp in (("Big Corp", "bigcorp.example", 5000),
                                     ("Enterprise Co", "enterprise.example", 9999),
                                     ("Mid Giant", "midgiant.example", 201)):
            fit, reason, detail = icp_fit.verdict(company, domain, employees=emp)
            self.assertFalse(fit, f"{company} should be off-ICP")
            self.assertEqual(reason, "too-large")
            self.assertIn("over the ICP ceiling", detail)

    def test_a_listed_brand_is_excluded_even_without_a_size(self):
        # Box and Atlassian are on the list (they were pitched on 2026-10-12) and the list
        # wins, so an unmeasurable listed name still cannot be drafted.
        for company, domain in (("Box", "box.com"), ("Atlassian", "atlassian.com"),
                                ("Anthropic", "anthropic.com")):
            fit, reason, _ = icp_fit.verdict(company, domain, cache={}, allow_fetch=False)
            self.assertFalse(fit, f"{company} is on the list")
            self.assertEqual(reason, "off-icp-list")

    def test_below_the_band_is_reported_but_kept(self):
        # A six-person company does buy newsletter ads; the ceiling is the only hard bound.
        fit, reason, detail = icp_fit.verdict("Tiny Doors", "tinydoors.com", employees=6)
        self.assertTrue(fit)
        self.assertEqual(reason, "below-band")
        self.assertIn("not dropped", detail)

    def test_unknown_size_is_kept_and_never_fetches_when_disallowed(self):
        fit, reason, detail = icp_fit.verdict("Mystery Co", "mystery.example",
                                              cache={}, allow_fetch=False)
        self.assertTrue(fit)
        self.assertEqual(reason, "size-unknown")
        self.assertIn("kept", detail)

    def test_a_subdomain_of_a_listed_parent_is_excluded(self):
        fit, reason, _ = icp_fit.verdict("Box", "mail.box.com", cache={}, allow_fetch=False)
        self.assertFalse(fit)
        self.assertEqual(reason, "off-icp-list")

    def test_the_hand_held_list_matches_domain_or_name(self):
        # The real list file is read by the rule, and a listed domain is not a fit even when
        # its size is unmeasurable.
        self.assertTrue(icp_fit.off_list_entries())
        self.assertTrue(icp_fit.on_off_list("Atlassian", "www.atlassian.com"))
        self.assertIsNone(icp_fit.on_off_list("Amberdata", "amberdata.io"))
        fit, reason, detail = icp_fit.verdict("Atlassian", "www.atlassian.com",
                                              cache={}, allow_fetch=False)
        self.assertFalse(fit)
        self.assertEqual(reason, "off-icp-list")
        self.assertIn("icp/off_icp.txt", detail)

    def test_cached_size_is_used_without_fetching(self):
        cache = {"acme-data.example": {"employees": 9999, "band": "5000 - 9999"}}
        fit, reason, _ = icp_fit.verdict("Acme Data", "acme-data.example",
                                         cache=cache, allow_fetch=False)
        self.assertFalse(fit)
        self.assertEqual(reason, "too-large")


class Report(unittest.TestCase):
    def test_the_rule_reads_the_live_off_list_file(self):
        self.assertIsInstance(icp_fit.off_list_entries(), list)

    def test_off_list_ignores_comments_and_blank_lines(self):
        path = icp_fit.OFF_LIST
        real = open(path).read() if os.path.exists(path) else ""
        try:
            with open(path, "w") as fh:
                fh.write("# a comment\n\natlassian.com\n  MongoDB  \n")
            entries = icp_fit.off_list_entries()
            self.assertEqual(entries, ["atlassian.com", "mongodb"])
            self.assertTrue(icp_fit.on_off_list("Atlassian", "www.atlassian.com"))
            self.assertTrue(icp_fit.on_off_list("MongoDB", "mongodb.com"))
            self.assertIsNone(icp_fit.on_off_list("Amberdata", "amberdata.io"))
        finally:
            with open(path, "w") as fh:
                fh.write(real)


if __name__ == "__main__":
    unittest.main(verbosity=2)
