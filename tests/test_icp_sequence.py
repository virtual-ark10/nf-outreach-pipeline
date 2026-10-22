#!/usr/bin/env python3
"""The ICP ladder, offline: what it may say to a company the corpus has no placement for.

The corpus ladder quotes the sponsor export. An ICP lead has no export row, so its copy stands
on three things only: the lists its lane is in, whether those lists take sponsors, and who is
running in them. These cases pin that, and pin the copy against the send gate's own language
rules, because the gate is what stands between a draft and an inbox.
"""
import os
import re
import json
import sys
import datetime
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
        "lead_source": "icp_research", "email": "wei@crustdata.com", "score": 98,
        # Every live ICP lead carries the domain its lane resolved (0 of 104 lack one), and the
        # gate holds a first touch whose lead has no domain to match the address against.
        "domain": "crustdata.com"}

# The same lead as the ICP research scored it for a data company: the segment is what routes it
# to the feed-first ladder, so the copy offers the API and the MCP server as well as the lists.
DATA_LEAD = dict(LEAD, notes='["ICP research 2026-10-08: strong (98.3/100), segment signal_data, '
                             '37 contact(s) found via free people-search lanes."]')


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
        # The company rides in the subject too, so a batch of lookalikes is not a batch of
        # identical subject lines.
        self.assertIn("Crustdata", self.subject)

    def test_it_never_claims_the_prospect_buys_anything(self):
        # The company's own name is allowed (it is the personalisation); a claim that they
        # sponsor, or that we saw them anywhere, is not.
        for word in ("you sponsor", "you ran", "saw you", "your placement", "placements in"):
            self.assertNotIn(word, self.text)
            self.assertNotIn(word, self.subject)

    def test_the_signature_stays_the_placeholder_the_pad_fills(self):
        self.assertTrue(self.text.rstrip().endswith("[Name], Founder, NewsletterFIT"))


class DataFirstLadder(unittest.TestCase):
    """A company that sells data gets the feed offered beside the lists, not instead of them."""

    def setUp(self):
        self.picks = icp.candidates(search_of(PUBS), ["developer tools"])[:3]

    def test_the_segment_in_the_icp_note_routes_the_lead_to_the_feed_ladder(self):
        self.assertTrue(icp.data_first(DATA_LEAD))
        self.assertFalse(icp.data_first(LEAD))

    def test_the_industry_label_is_the_fallback_when_no_segment_was_recorded(self):
        self.assertTrue(icp.data_first({"industry": "Data Infrastructure and Analytics"}))
        self.assertFalse(icp.data_first({"industry": "Transportation, Logistics and Storage"}))

    def test_touch_one_offers_the_api_and_mcp_in_one_sentence(self):
        subject, text = icp.first_touch(DATA_LEAD, self.picks)
        self.assertIn("REST API and an MCP server", text)
        self.assertIn("queried inside Crustdata", text)
        # The lists are still the proof: the offer adds a surface, it does not replace the hook.
        self.assertIn("Notion is buying in Refactoring", text)
        for p in self.picks:
            self.assertIn(f"- {p['name']} (est. {p['label']}): [TRACKED_LINK]", text)

    def test_touch_two_offers_the_same_fields_as_rows(self):
        _, text = icp.follow_up(DATA_LEAD, 2, self.picks)
        self.assertIn("If rows are easier to test than a profile", text)
        self.assertIn("API returns the same fields", text)

    def test_touch_three_names_the_tool_count_the_docs_and_the_endpoint(self):
        subject, text = icp.follow_up(DATA_LEAD, 3, self.picks)
        self.assertIn(f"{icp.MCP_TOOLS} tools", text)
        self.assertIn(icp.MCP_URL, text)
        self.assertIn(icp.API_DOCS, text)
        self.assertIn("trial key", text)
        # Still one tracked link, so the send carries attribution and the gate has a bullet it
        # can resolve against the corpus.
        self.assertEqual(text.count("[TRACKED_LINK]"), 1)

    def test_touch_four_asks_for_a_trial_key_rather_than_lists(self):
        _, text = icp.follow_up(DATA_LEAD, 4, self.picks[:1])
        self.assertIn("trial key and a sample export", text)
        self.assertEqual(text.count("[TRACKED_LINK]"), 1)

    def test_the_quoted_tool_count_is_the_one_the_product_publishes(self):
        # 17 was read from services/mcp/tools.js (TOOLS length) and from
        # newsletterfit.com/.well-known/agent.json (capabilities.actions length) on 2026-10-21.
        # If the product gains tools, this test is the reminder to re-read both before the
        # number goes back into an email.
        self.assertEqual(icp.MCP_TOOLS, 17)


class ResearchedFirstTouch(unittest.TestCase):
    """A hand-written first touch wins over the lane template, and only for touch 1."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.old = icp.CUSTOM_DIR
        icp.CUSTOM_DIR = self.tmp
        with open(os.path.join(self.tmp, "crustdata.json"), "w") as fh:
            json.dump({"subject": "A newsletter corpus for your fund customers",
                       "body": "Your docs describe a workflow's source as the set of URLs it "
                               "extracts from.\n\nWould a sample be useful?"}, fh)
        self.picks = icp.candidates(search_of(PUBS), ["developer tools"])[:3]

    def tearDown(self):
        import shutil
        icp.CUSTOM_DIR = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_touch_one_uses_the_researched_copy_verbatim(self):
        subject, text = icp.compose(LEAD, 1, self.picks)
        self.assertEqual(subject, "A newsletter corpus for your fund customers")
        self.assertIn("Your docs describe a workflow's source", text)
        self.assertIn("Would a sample be useful?", text)

    def test_the_greeting_signature_and_one_tracked_link_stay_generated(self):
        # The gate checks the greeting against the CRM contact and refuses a mail with no
        # tracked link, so neither may be baked into the JSON file.
        _, text = icp.compose(LEAD, 1, self.picks)
        self.assertTrue(text.startswith("Hi Wei,"))
        self.assertTrue(text.rstrip().endswith("[Name], Founder, NewsletterFIT"))
        self.assertEqual(text.count("[TRACKED_LINK]"), 1)

    def test_a_first_touch_file_leaves_the_follow_ups_alone(self):
        # The touch-1 file must never leak into touch 2: a follow-up that repeats the first
        # touch is not new information, it is the same mail twice.
        subject, text = icp.compose(LEAD, 2, self.picks)
        self.assertEqual(subject, f"who reads {self.picks[0]['name']}")
        self.assertNotIn("Would a sample be useful?", text)

    def test_a_per_touch_file_replaces_that_touch_only(self):
        # data/icp-custom/<id>.touch2.json is the only way a researched gap reaches a
        # follow-up, and it must not touch any other rung.
        with open(os.path.join(self.tmp, "crustdata.touch2.json"), "w") as fh:
            json.dump({"subject": "The gap, one rung later",
                       "body": "Your docs index companies, people, jobs and social posts.\\n\\n"
                               "Newsletter mentions are the fifth set."}, fh)
        subject, text = icp.compose(LEAD, 2, self.picks)
        self.assertEqual(subject, "The gap, one rung later")
        self.assertIn("Newsletter mentions are the fifth set.", text)
        self.assertTrue(text.startswith("Hi Wei,"))
        self.assertTrue(text.rstrip().endswith("[Name], Founder, NewsletterFIT"))
        self.assertEqual(text.count("[TRACKED_LINK]"), 1)
        # Touch 3 still comes from the ladder, and touch 1 still uses the plain file.
        self.assertEqual(icp.compose(LEAD, 3, self.picks)[0], f"the other side of "
                         f"{icp.lane_for(LEAD)[0]}")
        self.assertEqual(icp.compose(LEAD, 1, self.picks)[0],
                         "A newsletter corpus for your fund customers")

    def test_a_due_date_the_pad_left_null_still_opens_the_ladder(self):
        # The pad's DUE_DAYS has no entry for stage 'leads', and every ICP first touch is sent
        # from there, so next_follow_up_at stays null. The send date plus the stage's wait is
        # the fallback: without it no ICP lead ever becomes due for touch 2.
        lead = dict(LEAD, stage="first_email", next_follow_up_at=None,
                    last_contact_at="2026-10-22T07:00:42.738Z")
        self.assertEqual(icp.cadence_due(lead), "2026-10-25")
        lead["next_follow_up_at"] = "2026-11-01T00:00:00.000Z"
        self.assertEqual(icp.cadence_due(lead), "2026-11-01")
        self.assertEqual(icp.cadence_due(dict(LEAD, stage="first_email")), "")

    def test_a_missing_file_falls_back_to_the_lane_template(self):
        lead = dict(LEAD, id="nobody")
        subject, text = icp.compose(lead, 1, self.picks)
        self.assertIn("Notion is buying in Refactoring", text)

    def test_the_stored_researched_copies_pass_the_gate_s_language_rules(self):
        # data/icp-custom is hand-written, so it is the one place a banned phrase can slip in:
        # "a field you cannot fill today" sat in the Icypeas draft until this caught it. The
        # gate bans "today" because a queued draft sends days after it was written.
        import glob
        files = sorted(glob.glob(os.path.join(self.old, "*.json")))
        self.assertTrue(files, "no researched copies found")
        for path in files:
            d = json.load(open(path))
            blob = d["subject"] + "\n" + d["body"]
            self.assertNotIn(gate.EM_DASH, blob, path)
            for rx, label in ((gate.BACK_REFERENCE, "back-reference"),
                              (gate.STALE_DATE, "stale date"),
                              (gate.STALE_RELATIVE, "stale relative date"),
                              (gate.CORPUS_SIZE, "corpus size"),
                              (gate.UNVERIFIABLE_FIGURE, "unverifiable figure")):
                m = rx.search(blob)
                self.assertIsNone(m, f"{label} in {path}: {m.group(0) if m else ''}")


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


class TheRealGateWouldSendAnIcpDraft(unittest.TestCase):
    """Run the actual gate over the composed copy, with the pad's transformations applied.

    The regex assertions above are cheap; this is the real check. The gate decides whether a
    draft may leave the building, so an ICP touch is put through it here, with the tracked links
    the pad would mint and the corpus stub answering for the publications it names.
    """
    TOKEN = "bQ7fZ2mK4pRs9tVx1yA3"          # 20 chars, the shape the mint produces

    class Corpus:
        def placements(self, company):
            return None                       # an ICP lead has no export row: never claim one

        def publication(self, name):
            for p in PUBS:
                if p["name"].lower() == str(name).lower():
                    return {"subscribers": p["subscribers"],
                            "subscribersLabel": p["subscribersLabel"],
                            "recentSponsors": p["recentSponsors"]}
            return None

    def draft_for(self, picks, touch, lead=None):
        lead = dict(lead or LEAD, stage="leads", first_contact_at=None)
        subject, text = icp.compose(lead, touch, picks)
        text = re.sub(r"\s*:\s*\[TRACKED_LINK\]", "", text).replace("[Name]", "Ian Hinga")
        html = "<p>" + "</p><p>".join(
            f'<a href="https://newsletterfit.com/api/click?lt={self.TOKEN}">{p["name"]}</a>'
            f' (est. {p["label"]})' for p in picks) + "</p>"
        draft = {"id": icp.draft_id(lead, touch), "company": lead["company"], "to": lead["email"],
                 "from": "ian@newsletterfit.com", "subject": subject, "text": text, "html": html}
        ctx = {"ledger": {self.TOKEN: {"dest": "https://newsletterfit.com/app/publications/refactoring"}},
               "leads": {lead["id"]: lead}, "sent": {}, "replied": {},
               "by_company": {lead["id"]: [draft]}, "corpus": self.Corpus()}
        return draft, ctx

    def test_every_touch_passes_the_gate(self):
        picks = icp.candidates(search_of(PUBS), ["developer tools"])[:3]
        for touch in (1, 2, 3, 4):
            draft, ctx = self.draft_for(picks if touch != 4 else picks[:1], touch)
            verdict, findings = gate.gate(draft, ctx)
            self.assertEqual(verdict, "PASS",
                             f"touch {touch} came back {verdict}: {[f['message'] for f in findings]}")

    def test_the_feed_ladder_passes_the_gate_too(self):
        # The API and MCP sentence is exactly the kind of copy the gate could read as an
        # unverifiable claim, so the data ladder is put through it rather than assumed safe.
        picks = icp.candidates(search_of(PUBS), ["developer tools"])[:3]
        for touch, chosen in ((1, picks), (2, picks), (3, picks[:1]), (4, picks[:1])):
            draft, ctx = self.draft_for(chosen, touch, DATA_LEAD)
            verdict, findings = gate.gate(draft, ctx)
            self.assertEqual(verdict, "PASS",
                             f"data touch {touch} came back {verdict}: "
                             f"{[f['message'] for f in findings]}")

    def test_a_placement_count_would_be_the_one_thing_it_holds(self):
        # Pins why the copy must never state one: the gate looks the number up against the
        # lead's own export row, which an ICP lead does not have, and returns a REVIEW.
        picks = icp.candidates(search_of(PUBS), ["developer tools"])[:3]
        draft, ctx = self.draft_for(picks, 1)
        draft["text"] += "\n\nWe logged 2 placements there.\n"
        verdict, findings = gate.gate(draft, ctx)
        self.assertEqual(verdict, "REVIEW")
        self.assertIn("placements", " ".join(f["message"] for f in findings))


class TheLiveGapCopiesClearTheGate(unittest.TestCase):
    """The researched gap touches held in data/icp-custom, put through the real gate.

    These are the copies themselves, not fixtures. Common Room, Crustdata and Autobound had a
    researched gap written for them on 2026-10-21 and their first touches still went out on the
    lane template, because the copy was never loaded into this directory. Loading it is half the
    fix; this class is the other half, because a hand-written file is the one place a phrase the
    gate bans can slip into the queue.
    """
    TOKEN = "bQ7fZ2mK4pRs9tVx1yA3"
    Corpus = TheRealGateWouldSendAnIcpDraft.Corpus
    LEADS = [
        {"id": "common-room", "company": "Common Room", "contact_name": "Dan Brayton",
         "contact_title": "VP of Revenue Operations", "email": "danbrayton@commonroom.io",
         "domain": "commonroom.io", "industry": "Software Development", "score": 98,
         "lead_source": "icp_research", "last_contact_at": "2026-10-22T07:00:39.803Z",
         "notes": '["ICP research 2026-10-08: strong (98.3/100), segment signal_data"]'},
        {"id": "crustdata", "company": "Crustdata", "contact_name": "Andres Pimentel",
         "contact_title": "Founding GTM", "email": "andres@crustdata.com",
         "domain": "crustdata.com", "industry": "Software Development", "score": 98,
         "lead_source": "icp_research", "last_contact_at": "2026-10-22T07:00:41.280Z",
         "notes": '["ICP research 2026-10-08: strong (98.3/100), segment signal_data"]'},
        {"id": "autobound", "company": "Autobound", "contact_name": "Kyle Schuster",
         "contact_title": "Co-Founder (Sales and Product)", "email": "kyle@autobound.ai",
         "domain": "autobound.ai", "industry": "Software Development", "score": 81,
         "lead_source": "icp_research", "last_contact_at": "2026-10-22T07:00:42.738Z",
         "notes": '["ICP validation 2026-10-21: gap confirmed (35+ documented sources)"]'},
    ]

    def draft_for(self, picks, touch, lead=None):
        if lead is None:
            raise ValueError("the live gap copies are gated for a named lead, not a fixture")
        # As the CRM holds them after the first touch: contacted, cadence open, stage
        # first_email. Gating an earlier state would test a mail that can no longer be sent.
        lead = dict(lead, stage="first_email", first_contact_at=lead["last_contact_at"],
                    next_follow_up_at=datetime.date.today().isoformat())
        subject, text = icp.compose(lead, touch, picks)
        text = re.sub(r"\s*:\s*\[TRACKED_LINK\]", "", text).replace("[Name]", "Ian Hinga")
        html = "<p>" + "</p><p>".join(
            f'<a href="https://newsletterfit.com/api/click?lt={self.TOKEN}">{p["name"]}</a>'
            f' (est. {p["label"]})' for p in picks) + "</p>"
        draft = {"id": icp.draft_id(lead, touch), "company": lead["company"], "to": lead["email"],
                 "from": "ian@newsletterfit.com", "subject": subject, "text": text, "html": html}
        ctx = {"ledger": {self.TOKEN: {
                   "dest": "https://newsletterfit.com/app/publications/refactoring"}},
               # The gate keys both maps by the lowercased company name, not by lead id.
               "leads": {lead["company"].lower(): lead}, "sent": {}, "replied": {},
               "by_company": {lead["company"].lower(): [draft]}, "corpus": self.Corpus()}
        return draft, ctx

    def test_both_rungs_pass_the_real_gate(self):
        picks = icp.candidates(search_of(PUBS), ["developer tools"])[:3]
        for lead in self.LEADS:
            for touch in (1, 2):
                self.assertIsNotNone(icp.custom_copy(lead, touch),
                                     f"no researched copy for {lead['id']} touch {touch}")
                draft, ctx = self.draft_for(picks, touch, lead)
                verdict, findings = gate.gate(draft, ctx)
                self.assertEqual(
                    verdict, "PASS",
                    f"{lead['id']} touch {touch} came back {verdict}: "
                    f"{[f['message'] for f in findings]}")

    def test_the_follow_up_says_something_the_first_touch_did_not(self):
        for lead in self.LEADS:
            one = icp.custom_copy(lead, 1)["body"]
            two = icp.custom_copy(lead, 2)["body"]
            self.assertNotEqual(one[:80], two[:80], f"{lead['id']} repeats its first touch")


if __name__ == "__main__":
    unittest.main(verbosity=2)
