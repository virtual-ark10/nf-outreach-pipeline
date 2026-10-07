#!/usr/bin/env python3
"""The NewsletterFIT supply scripts, offline.

Two jobs keep the uncontacted pool fed: import_candidates (the corpus export's qualified
companies become CRM leads, with a domain that the search route has to confirm) and
discover_contacts (an address and a name for the leads that lack them). Both lean on the
shared brand rules in the local-SEO pipeline's site_match.py. These cases pin the
decisions, not the plumbing: which candidates qualify, which leads are worth working, and
when a domain is refused because it is not the brand's own.
"""
import os
import sys
import unittest
from unittest import mock

NF_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(NF_REPO, "scripts"))

import discover_contacts as dc   # noqa: E402
import import_candidates as ic   # noqa: E402


class ExportRule(unittest.TestCase):
    """The intake rule: MEDIUM or better, at least one placement (was >= 2)."""

    ROWS = [
        {"sponsor": "Tracksuit", "outreachQuality": "HIGH", "placements": "27", "sponsorScore": "100"},
        {"sponsor": "Wispr Flow", "outreachQuality": "MEDIUM", "placements": "1", "sponsorScore": "74"},
        {"sponsor": "Thin Sponsor", "outreachQuality": "MEDIUM", "placements": "0", "sponsorScore": "60"},
        {"sponsor": "Nobody", "outreachQuality": "LOW", "placements": "9", "sponsorScore": "40"},
    ]

    def qualify(self, rows, min_quality="MEDIUM", min_placements=1, have=()):
        floor = ic.QUALITY_ORDER[min_quality]
        out = []
        for r in rows:
            q = str(r.get("outreachQuality") or "").upper()
            placements = int(float(r.get("placements") or 0))
            name = str(r.get("sponsor") or "")
            if ic.QUALITY_ORDER.get(q, -1) < floor or placements < min_placements:
                continue
            if ic.norm(name) in have:
                continue
            out.append(name)
        return out

    def test_the_widened_rule_takes_one_placement_but_not_zero(self):
        self.assertEqual(self.qualify(self.ROWS), ["Tracksuit", "Wispr Flow"])

    def test_the_old_rule_still_works_when_asked_for_it(self):
        self.assertEqual(self.qualify(self.ROWS, min_placements=2), ["Tracksuit"])
        self.assertEqual(self.qualify(self.ROWS, min_quality="HIGH"), ["Tracksuit"])

    def test_a_company_already_in_the_crm_is_not_a_candidate(self):
        self.assertEqual(self.qualify(self.ROWS, have={ic.norm("Tracksuit")}), ["Wispr Flow"])


class PublicationLists(unittest.TestCase):
    def test_the_exports_human_joined_lists_are_split(self):
        self.assertEqual(
            ic.split_list("WHAT'S ANU, The Brand Waves, and Famous Campaigns"),
            ["WHAT'S ANU", "The Brand Waves", "Famous Campaigns"])
        self.assertEqual(ic.split_list(""), [])
        self.assertEqual(ic.split_list(["A", " B "]), ["A", "B"])


class DomainGuessing(unittest.TestCase):
    def test_the_brand_name_is_tried_before_its_variants(self):
        guesses = ic.guesses_for("CodeRabbit", "coderabbit")
        self.assertEqual(guesses[0], "coderabbit.com")
        self.assertIn("coderabbit.io", guesses)
        self.assertIn("getcoderabbit.com", guesses)
        self.assertIn("coderabbithq.com", guesses)


class BrandRulesAreShared(unittest.TestCase):
    def test_a_namesake_host_is_refused_even_when_the_page_says_the_word(self):
        # The Anam case: the sponsor is anam.ai, the guess was anam.com.
        self.assertTrue(ic.label_is_brand("anam.ai", "Anam", "anam"))
        self.assertFalse(ic.label_is_brand("apolloglobalmanagement.com", "Apollo", "apollo"))
        self.assertTrue(ic.label_is_brand("getwhys.com", "GetWhys", "getwhys"))

    def test_a_brands_own_subdomain_of_a_parent_is_accepted(self):
        # AWS's site is aws.amazon.com: the label is the parent's, the brand is the subdomain.
        self.assertTrue(ic.label_is_brand("aws.amazon.com", "AWS", "aws"))
        self.assertFalse(ic.label_is_brand("aws.amazon.com", "Sign", "sign"))

    def test_a_page_has_to_carry_every_brand_word(self):
        page = "<html><body>Bar Harbor Bicycle Shop, Maine</body></html>"
        self.assertTrue(ic.page_names_brand(page, "Bar Harbor Bicycle Shop"))
        self.assertFalse(ic.page_names_brand("<html>Bar Harbor Rentals</html>",
                                             "Bar Harbor Bicycle Shop"))
        self.assertFalse(ic.page_names_brand("", "Anam"))


class Targets(unittest.TestCase):
    LEADS = [
        {"id": "a", "company": "No Address", "domain": "a.com", "stage": "leads", "email": None},
        {"id": "b", "company": "Address No Name", "domain": "b.com", "stage": "leads",
         "email": "info@b.com", "contact_name": None},
        {"id": "c", "company": "Complete", "domain": "c.com", "stage": "leads",
         "email": "info@c.com", "contact_name": "Jane Doe"},
        {"id": "d", "company": "Already Emailed", "domain": "d.com", "stage": "first_email",
         "email": "info@d.com"},
        {"id": "e", "company": "Unsubscribed", "domain": "e.com", "stage": "leads",
         "email": "info@e.com", "unsubscribed": 1},
        {"id": "f", "company": "No Domain", "domain": None, "stage": "leads", "email": None},
        {"id": "g", "company": "Bounced", "domain": "g.com", "stage": "leads",
         "email": "info@g.com", "bounced": 1},
    ]

    def test_every_lead_that_cannot_be_drafted_yet_is_a_target(self):
        ids = [l["id"] for l in dc.targets_from(self.LEADS, 20)]
        self.assertEqual(ids, ["a", "b"])          # no address first, then no name

    def test_contacted_suppressed_and_domainless_leads_are_left_alone(self):
        ids = [l["id"] for l in dc.targets_from(self.LEADS, 20)]
        for left in ("c", "d", "e", "f", "g"):
            self.assertNotIn(left, ids)

    def test_the_run_limit_is_respected(self):
        self.assertEqual(len(dc.targets_from(self.LEADS, 1)), 1)


class DomainConfirmation(unittest.TestCase):
    """Nothing is spent on a domain that is not the brand's own."""

    def check(self, pages):
        with mock.patch.object(dc.contact_finder, "get_text", lambda url, timeout=15: pages.get(url)):
            return dc.domain_confirms_brand("example.com", "Example Sponsor")

    def test_a_page_that_names_the_brand_confirms_the_domain(self):
        ok, why = self.check({"https://example.com": "<html>Example Sponsor Ltd</html>"})
        self.assertTrue(ok)
        self.assertEqual(why, "")

    def test_a_namesake_site_is_refused(self):
        ok, why = self.check({"https://example.com": "<html>Somebody Else Inc</html>"})
        self.assertFalse(ok)
        self.assertIn("does not name the brand", why)

    def test_a_site_that_cannot_be_loaded_is_not_a_confirmation(self):
        ok, why = self.check({})
        self.assertFalse(ok)
        self.assertIn("could not load", why)

    def test_a_blocked_homepage_falls_through_to_the_contact_page(self):
        ok, _ = self.check({"https://example.com/contact": "<html>Example Sponsor, contact us</html>"})
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
