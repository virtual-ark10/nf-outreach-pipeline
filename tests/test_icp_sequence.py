#!/usr/bin/env python3
"""The ICP ladder, offline: what it may say to a company the corpus has no placement for.

The corpus ladder quotes the sponsor export. An ICP lead has no export row, so its copy stands
on three things only: the lists its lane is in, whether those lists take sponsors, and who is
running in them. These cases pin that, and pin the copy against the send gate's own language
rules, because the gate is what stands between a draft and an inbox.
"""
import os
import re
import sys
import unittest

NF_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(NF_REPO, "scripts"))

import nf_send_gate as gate      # noqa: E402
import seed_icp_drafts as icp    # noqa: E402


def pub(name, slug, label, subs, sponsors=(), mentions=0, accepts=True, freq="weekly"):
    return {"name": name, "slug": slug, "subscribersLabel": label, "subscribers": subs,
            "recentSponsors": list(sponsors), "sponsorMentions": mentions,
            "acceptsSponsors": accepts, "frequency": freq, "category": "Technology"}


PUBS = [
    pub("Refactoring", "refactoring", "173K", 173000, ["Notion", "Unblocked"], 6),
    pub("Ravi on Product", "ravi-on-product", "32K", 32000, ["Atlassian"], 1),
    pub("Data Engineering Central", "data-engineering-central", "23K", 23000, ["Delta", "Cube"], 6),
    pub("Quiet List", "quiet-list", "50K", 50000, [], 0),
    pub("Huge Quiet List", "huge-quiet-list", "400K", 400000, [], 0),
    pub("No Sponsor Slot", "no-sponsor-slot", "99K", 99000, [], 4, accepts=False),
    pub("Nameless", "nameless", "", 0, [], 0),
]

LEAD = {"id": "crustdata", "company": "Crustdata", "contact_name": "Wei Zhang",
        "contact_title": "Head of Growth", "industry": "Software Development",
        "lead_source": "icp_research", "email": "wei@crustdata.com", "score": 98}


def search_of(pubs):
    def search(q):
        return {"newsletters": [dict(p, _q=q) for p in pubs]}
    return search


class CandidateSelection(unittest.TestCase):
    def test_only_lists_that_take_sponsors_are_candidates(self):
        names = [p["name"] for p in icp.candidates(search_of(PUBS), ["developer tools"])]
        self.assertIn("Refactoring", names)
        self.assertNotIn("No Sponsor Slot", names)

    def test_a_list_with_named_sponsors_comes_before_a_bigger_one_without(self):
        # The copy claims a list takes sponsors, so a list with names running in it is the
        # honest thing to lead with, even against a much larger list that only says it would.
        picks = icp.candidates(search_of(PUBS), ["developer tools"])
        order = [p["name"] for p in picks]
        self.assertEqual(order[0], "Refactoring")
        self.assertLess(order.index("Refactoring"), order.index("Huge Quiet List"))

    def test_a_list_without_a_readable_size_is_not_usable(self):
        names = [p["name"] for p in icp.candidates(search_of(PUBS), ["x"])]
        self.assertNotIn("Nameless", names)

    def test_the_same_list_from_two_queries_is_offered_once(self):
        picks = icp.candidates(search_of(PUBS), ["developer tools", "software engineering"])
        self.assertEqual(len(picks), len({p["slug"] for p in picks}))

    def test_rotation_walks_the_pool_so_a_batch_is_not_all_the_same_names(self):
        picks = icp.candidates(search_of(PUBS), ["developer tools"])
        first = [p["name"] for p in icp.rotate(picks, 0, 3)]
        second = [p["name"] for p in icp.rotate(picks, 1, 3)]
        self.assertEqual(len(first), 3)
        self.assertNotEqual(first, second)

    def test_a_short_pool_is_returned_whole_rather_than_padded(self):
        picks = icp.candidates(search_of(PUBS), ["developer tools"])[:2]
        self.assertEqual(len(icp.rotate(picks, 5, 3)), 2)


class Lane(unittest.TestCase):
    def test_the_leads_own_market_decides_the_lane(self):
        label, queries = icp.lane_for({"industry": "Data Infrastructure and Analytics"})
        self.assertEqual(label, "data infrastructure")
        self.assertIn("data engineering", queries)

    def test_an_unknown_industry_falls_back_rather_than_searching_nothing(self):
        label, queries = icp.lane_for({"industry": "N/A"})
        self.assertTrue(queries)
        self.assertEqual(label, icp.DEFAULT_LANE[0])


class FirstTouch(unittest.TestCase):
    def setUp(self):
        self.picks = icp.candidates(search_of(PUBS), ["developer tools"])[:3]
        self.subject, self.text = icp.first_touch(LEAD, self.picks)

    def test_it_greets_the_crm_contact_by_first_name(self):
        self.assertTrue(self.text.startswith("Hi Wei,"))

    def test_every_list_is_a_bullet_with_a_tracked_link_and_its_size(self):
        for p in self.picks:
            self.assertIn(f"- {p['name']} (est. {p['label']}): [TRACKED_LINK]", self.text)

    def test_the_hook_is_a_sponsor_the_corpus_actually_logged_in_that_list(self):
        self.assertIn("Notion is buying in Refactoring", self.text)
        self.assertIn("Notion", self.subject)

    def test_it_never_claims_the_prospect_buys_anything(self):
        self.assertNotIn("Crustdata", self.subject)
        for word in ("you sponsor", "you ran", "saw you", "your placement", "placements in"):
            self.assertNotIn(word, self.text)

    def test_the_signature_stays_the_placeholder_the_pad_fills(self):
        self.assertTrue(self.text.rstrip().endswith("[Name], Founder, NewsletterFIT"))


class Ladder(unittest.TestCase):
    def setUp(self):
        self.picks = icp.candidates(search_of(PUBS), ["developer tools"])[:3]

    def test_touch_two_details_the_list_touch_one_led_on(self):
        subject, text = icp.follow_up(LEAD, 2, self.picks)
        self.assertEqual(subject, f"who reads {self.picks[0]['name']}")
        self.assertIn("173K readers", text)
        self.assertIn("Notion", text)

    def test_touch_three_names_the_other_lists_and_their_sponsors(self):
        _, text = icp.follow_up(LEAD, 3, self.picks)
        self.assertIn("Delta", text)
        self.assertEqual(text.count("[TRACKED_LINK]"), len(self.picks))

    def test_touch_four_closes_with_one_list_still_attached(self):
        _, text = icp.follow_up(LEAD, 4, self.picks[:1])
        self.assertEqual(text.count("[TRACKED_LINK]"), 1)
        self.assertIn("I will leave it here", text)

    def test_the_ids_match_the_naming_the_ladder_already_uses(self):
        self.assertEqual(icp.draft_id(LEAD, 1), "crustdata")
        self.assertEqual(icp.draft_id(LEAD, 3), "crustdata-follow-up-3")


class TheGateWouldNotHoldThisCopy(unittest.TestCase):
    """The gate is the last thing before an inbox, so its rules are asserted here, not trusted."""

    def setUp(self):
        picks = icp.candidates(search_of(PUBS), ["developer tools"])[:3]
        self.drafts = [(icp.first_touch(LEAD, picks))]
        self.drafts += [icp.follow_up(LEAD, t, picks) for t in (2, 3, 4)]

    def test_no_em_dash_anywhere(self):
        for subject, text in self.drafts:
            self.assertNotIn(gate.EM_DASH, subject + text)

    def test_no_back_reference_or_apology_language(self):
        for subject, text in self.drafts:
            for rx in (gate.BACK_REFERENCE,):
                for m in rx.finditer(text + " " + subject):
                    self.fail(f"{m.group(0)!r} in {subject!r}")

    def test_no_stale_time_reference(self):
        for subject, text in self.drafts:
            for rx in (gate.STALE_DATE, gate.STALE_RELATIVE):
                for m in rx.finditer(text + " " + subject):
                    self.fail(f"{m.group(0)!r} in {subject!r}")

    def test_no_corpus_size_or_unverifiable_figure(self):
        for subject, text in self.drafts:
            self.assertIsNone(gate.CORPUS_SIZE.search(text))
            self.assertIsNone(gate.UNVERIFIABLE_FIGURE.search(text))

    def test_no_number_is_ever_attached_to_the_word_placements(self):
        # The gate checks a stated count against the CRM lead's own export row, which an ICP
        # lead does not have, so any count here would be a REVIEW the copy cannot earn.
        for subject, text in self.drafts:
            for m in re.finditer(r"(\d+)\s+(?:[A-Za-z0-9.'&\s]{0,24}?)(?:placements|sponsorships)", text):
                self.fail(f"placement count claim: {m.group(0)!r}")

    def test_no_placeholder_survives_except_the_link_and_the_name(self):
        for subject, text in self.drafts:
            leftovers = re.findall(r"\[(?!TRACKED_LINK\]|Name\])[^\]]{1,20}\]", text)
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
