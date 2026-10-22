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

    def test_a_name_that_is_already_a_domain_is_tried_literally_first(self):
        # The export writes a few sponsors this way; "fastspringgg.com" is a wasted fetch.
        self.assertEqual(ic.guesses_for("FastSpring.gg")[0], "fastspring.gg")
        self.assertEqual(ic.guesses_for("Name.com")[0], "name.com")
        self.assertEqual(ic.guesses_for("CodeRabbit")[0], "coderabbit.com")


class BrandHostMatching(unittest.TestCase):
    """A brand that carries its own TLD still owns its domain."""

    def test_a_dotted_brand_name_matches_its_whole_host(self):
        self.assertTrue(ic.brand_host("name.com", "Name.com"))
        self.assertTrue(ic.brand_host("name.com", "Name.com", "name-com"))

    def test_a_multi_word_brand_split_across_label_and_tld_matches(self):
        # GroundNews -> ground.news: the label is "ground", the TLD carries "news".
        self.assertTrue(ic.brand_host("ground.news", "GroundNews"))
        self.assertTrue(ic.brand_host("www.ground.news", "GroundNews"))

    def test_the_whole_host_rule_does_not_loosen_the_namesake_check(self):
        self.assertFalse(ic.brand_host("apolloglobalmanagement.com", "Apollo", "apollo"))
        self.assertFalse(ic.brand_host("name.com", "Namesco", "namesco"))


class NameSpecificity(unittest.TestCase):
    """Which names may be resolved on the name alone when the page will not load."""

    def test_brands_are_specific(self):
        for name in ("Converse", "Masterclass", "Adobe", "SAP", "Wine Library"):
            self.assertTrue(ic.name_is_specific(name), name)

    def test_scraped_fragments_are_not(self):
        for name in ("State", "One", "Built", "America", "In Today's Issue", "", "  "):
            self.assertFalse(ic.name_is_specific(name), name)


class BlockedPageResolution(unittest.TestCase):
    """A site that refuses the fetcher is not proof the domain is wrong."""

    def resolve(self, hosts, pages, name, slug="", budget=None):
        with mock.patch.object(ic, "searx_hosts", lambda n, limit=8: hosts), \
             mock.patch.object(ic, "serp_hosts", lambda n, limit=8: ([], 0.0)), \
             mock.patch.object(ic, "record_serps", lambda cost, calls: None), \
             mock.patch.object(ic, "fetch", lambda url, timeout=12: pages.get(url, "")):
            return ic.resolve_domain(name, slug, budget or {"serps_left": 0})

    def test_an_exact_host_is_accepted_when_the_page_cannot_be_read(self):
        domain, evidence = self.resolve(["converse.com"], {}, "Converse", "converse")
        self.assertEqual(domain, "converse.com")
        self.assertIn("could not be fetched", evidence)

    def test_a_vague_name_still_needs_a_readable_page(self):
        domain, _ = self.resolve(["state.com"], {}, "State", "state")
        self.assertEqual(domain, "")

    def test_a_readable_page_that_does_not_name_the_brand_is_still_refused(self):
        domain, _ = self.resolve(["apolloglobalmanagement.com"],
                                 {"https://apolloglobalmanagement.com/": "<html>Other Co</html>"},
                                 "Apollo", "apollo")
        self.assertEqual(domain, "")

    def test_a_namesake_label_is_not_accepted_on_a_blocked_page(self):
        # Two branded candidates, neither an exact whole-host match: no guess, no send.
        domain, _ = self.resolve(["getconverse.com", "converseshoes.com"], {}, "Converse", "")
        self.assertEqual(domain, "")

    def test_a_lower_ranked_namesake_never_beats_the_brands_own_blocked_domain(self):
        # converse.com ranks first and refuses the fetcher; converse.edu is a university
        # whose page names "Converse". The second choice must not be imported as the brand.
        domain, evidence = self.resolve(
            ["converse.com", "converse.edu"],
            {"https://converse.edu/": "<html>Converse University</html>"},
            "Converse", "converse")
        self.assertEqual(domain, "converse.com")
        self.assertIn("could not be fetched", evidence)

    def test_the_readable_page_path_is_unchanged(self):
        domain, evidence = self.resolve(["coderabbit.ai"],
                                        {"https://coderabbit.ai/": "<html>CodeRabbit</html>"},
                                        "CodeRabbit", "coderabbit")
        self.assertEqual(domain, "coderabbit.ai")
        self.assertIn("names the brand", evidence)


class SearchResultShapes(unittest.TestCase):
    """The paid route answers with url/domain, not link. Reading the wrong key threw every
    lookup away while still charging for it."""

    def test_the_treg_route_shape_is_read(self):
        row = {"url": "https://www.sap.com/index.html", "domain": "www.sap.com"}
        self.assertEqual(ic._host_of(row), "sap.com")

    def test_a_bare_domain_is_read(self):
        self.assertEqual(ic._host_of({"domain": "help.sap.com"}), "sap.com")

    def test_the_older_link_shape_still_works(self):
        self.assertEqual(ic._host_of({"link": "https://mubi.com/en"}), "mubi.com")

    def test_a_result_with_nothing_usable_is_skipped(self):
        self.assertEqual(ic._host_of({}), "")


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
        {"id": "a", "company": "No Address", "domain": "a.com", "stage": "leads",
         "email": None, "priority": "MEDIUM"},
        {"id": "b", "company": "Address No Name", "domain": "b.com", "stage": "leads",
         "email": "jane@b.com", "contact_name": None, "priority": "HIGH"},
        {"id": "c", "company": "Complete", "domain": "c.com", "stage": "leads",
         "email": "jane@c.com", "contact_name": "Jane Doe", "priority": "HIGH"},
        {"id": "d", "company": "Already Emailed", "domain": "d.com", "stage": "first_email",
         "email": "jane@d.com"},
        {"id": "e", "company": "Unsubscribed", "domain": "e.com", "stage": "leads",
         "email": "jane@e.com", "unsubscribed": 1},
        {"id": "f", "company": "No Domain", "domain": None, "stage": "leads", "email": None},
        {"id": "g", "company": "Bounced", "domain": "g.com", "stage": "leads",
         "email": "jane@g.com", "bounced": 1},
        # Holds a shared mailbox and a name: complete on paper, unsendable in fact, because the
        # draft would greet Jane and land in the support queue.
        {"id": "h", "company": "Queue Mailbox", "domain": "h.com", "stage": "leads",
         "email": "support@h.com", "contact_name": "Jane Doe", "priority": "HIGH"},
    ]

    def test_every_lead_that_cannot_be_drafted_yet_is_a_target(self):
        ids = [l["id"] for l in dc.targets_from(self.LEADS, 20)]
        self.assertEqual(ids, ["a", "h", "b"])   # no usable address, then no name

    def test_a_shared_mailbox_does_not_count_as_an_address(self):
        ids = [l["id"] for l in dc.targets_from(self.LEADS, 20)]
        self.assertIn("h", ids)

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


class HeldBack(unittest.TestCase):
    """--skip is the operator's hand on the queue, not a rule the run can override."""

    CANDS = [(3, "MEDIUM", "Affinity", {}), (2, "MEDIUM", "Liberty Capital", {}),
             (1, "MEDIUM", "FanDuel", {})]

    def test_an_empty_skip_keeps_everything(self):
        kept, dropped = ic.drop_held(self.CANDS, "")
        self.assertEqual(len(kept), 3)
        self.assertEqual(dropped, 0)

    def test_named_companies_are_dropped_and_counted(self):
        kept, dropped = ic.drop_held(self.CANDS, "Affinity,Liberty Capital")
        self.assertEqual([c[2] for c in kept], ["FanDuel"])
        self.assertEqual(dropped, 2)

    def test_case_spacing_and_punctuation_do_not_matter(self):
        kept, dropped = ic.drop_held(self.CANDS, "  liberty-capital , AFFINITY ")
        self.assertEqual([c[2] for c in kept], ["FanDuel"])
        self.assertEqual(dropped, 2)

    def test_a_name_not_in_the_run_is_harmless(self):
        kept, dropped = ic.drop_held(self.CANDS, "Somebody Else")
        self.assertEqual(len(kept), 3)
        self.assertEqual(dropped, 0)


class PinnedDomains(unittest.TestCase):
    """A human-confirmed domain beats a search that ranked a namesake first."""

    def test_a_pair_is_read_as_company_and_domain(self):
        self.assertEqual(ic.pinned_domains("Affinity=affinity.co"), {"affinity": "affinity.co"})

    def test_several_pairs_and_awkward_spacing(self):
        pins = ic.pinned_domains(" Affinity = affinity.co , Liberty Capital=libertycapital.bank ")
        self.assertEqual(pins, {"affinity": "affinity.co",
                                "libertycapital": "libertycapital.bank"})

    def test_junk_is_ignored_rather_than_guessed(self):
        self.assertEqual(ic.pinned_domains("Affinity,=,=nope.com,Affinity=localhost"), {})

    def test_an_empty_spec_pins_nothing(self):
        self.assertEqual(ic.pinned_domains(""), {})


class HoldFile(unittest.TestCase):
    """A durable hold is a file edit, not a flag the daily job has to remember."""

    def names(self, body):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write(body)
            path = fh.name
        try:
            return ic.hold_file_names(path)
        finally:
            os.unlink(path)

    def test_comments_and_blank_lines_are_ignored(self):
        self.assertEqual(self.names("# a note\n\nLiberty Capital\n   \n# another\n"),
                         ["Liberty Capital"])

    def test_a_trailing_comment_is_not_part_of_the_name(self):
        self.assertEqual(self.names("Liberty Capital   # identity unresolved\n"), ["Liberty Capital"])

    def test_a_missing_file_is_not_an_error(self):
        self.assertEqual(ic.hold_file_names("/nonexistent/intake-hold.txt"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
