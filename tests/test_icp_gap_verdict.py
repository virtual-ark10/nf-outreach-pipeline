#!/usr/bin/env python3
"""The gap gate: may researched copy be drafted, and on what evidence?

The rule these tests pin: copy may only assert that a vendor lacks something when every quote used
as evidence was found on the page it cites. A quote that is not on its page blocks the account. A
quote that could not be fetched is not a refutation, so it holds the account until the verifier is
re-run. An unverified gap may still be emailed, but only in the offer-the-data form. And a custom
copy file with no research record behind it does not go out at all.

The last test runs the gate over the real tree (data/icp-custom against data/icp-gap), which is
versioned in this repo, so a future seeder change cannot quietly start trusting the copy that the
2026-10-21 audit's own evidence refutes.
"""
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import _gap_verdict as gv      # noqa: E402


def entry(rows):
    rows = [dict(r) for r in rows]
    return {"checked": len(rows), "verified": sum(1 for r in rows if r.get("ok")), "rows": rows}


OK = {"url": "https://a.example/", "quote": "q", "ok": True, "reason": "exact"}
MISS = {"url": "https://a.example/", "quote": "q", "ok": False,
        "reason": "not found (longest leading match 0/12 words)"}
UNFETCHED = {"url": "https://a.example/", "quote": "q", "ok": False,
             "reason": "firecrawl failed: HTTP Error 429: Too Many Requests"}
BLOCKED = {"url": "https://a.example/", "quote": "q", "ok": False,
           "reason": "__FETCH_FAILED__ HTTP Error 403: Forbidden"}


def record(gap, company="Acme"):
    return {"company": company, "gap_status": gap, "newsletter_capability_status":
            "not_found_in_public_docs", "verified_gap": "x", "evidence": []}


class Classify(unittest.TestCase):
    def test_a_found_quote_is_verified(self):
        self.assertEqual(gv.classify(OK), "verified")

    def test_a_quote_absent_from_the_page_is_refuted(self):
        self.assertEqual(gv.classify(MISS), "refuted")

    def test_a_failed_fetch_is_not_a_refutation(self):
        self.assertEqual(gv.classify(UNFETCHED), "unchecked")
        self.assertEqual(gv.classify(BLOCKED), "unchecked")

    def test_missing_url_or_quote_is_refuted(self):
        self.assertEqual(gv.classify({"url": None, "quote": None, "ok": False,
                                      "reason": "missing url or quote"}), "refuted")


class Verdicts(unittest.TestCase):
    def test_confirmed_and_all_verified_is_draftable(self):
        v = gv.verdict(record("confirmed"), entry([OK, OK]))
        self.assertEqual(v["status"], gv.VERIFIED)

    def test_confirmed_with_one_refuted_quote_is_blocked(self):
        v = gv.verdict(record("confirmed"), entry([OK, MISS]))
        self.assertEqual(v["status"], gv.BLOCKED)
        self.assertIn("not found on the cited page", v["reason"])

    def test_confirmed_with_no_report_is_blocked(self):
        v = gv.verdict(record("confirmed"), None)
        self.assertEqual(v["status"], gv.BLOCKED)
        self.assertIn("no quote verification", v["reason"])

    def test_confirmed_with_no_quotes_cited_is_blocked(self):
        v = gv.verdict(record("confirmed"), entry([]))
        self.assertEqual(v["status"], gv.BLOCKED)

    def test_confirmed_with_an_unfetched_quote_is_held_not_blocked(self):
        v = gv.verdict(record("confirmed"), entry([OK, UNFETCHED]))
        self.assertEqual(v["status"], gv.UNCHECKED)
        self.assertIn("re-run the verifier", v["reason"])

    def test_unverified_gap_with_verified_quotes_is_ask_only(self):
        v = gv.verdict(record("unverified"), entry([OK]))
        self.assertEqual(v["status"], gv.ASK_ONLY)

    def test_unverified_gap_still_blocks_on_a_refuted_quote(self):
        v = gv.verdict(record("unverified"), entry([MISS]))
        self.assertEqual(v["status"], gv.BLOCKED)

    def test_disconfirmed_gap_is_blocked_whatever_the_quotes(self):
        v = gv.verdict(record("disconfirmed"), entry([OK, OK]))
        self.assertEqual(v["status"], gv.BLOCKED)
        self.assertIn("disconfirmed", v["reason"])

    def test_no_research_record_is_unresearched(self):
        v = gv.verdict(None, entry([OK]))
        self.assertEqual(v["status"], gv.UNRESEARCHED)

    def test_a_broken_record_is_blocked(self):
        v = gv.verdict({"_broken": "Expecting value: line 1 column 1"})
        self.assertEqual(v["status"], gv.BLOCKED)

    def test_an_empty_gap_status_is_blocked(self):
        v = gv.verdict(record(""), entry([OK]))
        self.assertEqual(v["status"], gv.BLOCKED)


class Gate(unittest.TestCase):
    def setUp(self):
        import json
        import tempfile
        self.dir = tempfile.mkdtemp()
        self.custom = os.path.join(self.dir, "custom")
        self.research = os.path.join(self.dir, "research")
        os.makedirs(self.custom)
        os.makedirs(self.research)
        self.report = os.path.join(self.dir, "verify-report.json")
        with open(os.path.join(self.custom, "acme.json"), "w") as fh:
            json.dump({"subject": "s", "body": "b"}, fh)
        with open(os.path.join(self.research, "acme.json"), "w") as fh:
            json.dump(record("confirmed"), fh)
        with open(self.report, "w") as fh:
            json.dump({"Acme": entry([MISS])}, fh)

    def test_a_lead_without_custom_copy_is_not_judged(self):
        gate = gv.GapGate(self.custom, self.research, self.report)
        self.assertIsNone(gate.check({"id": "other", "company": "Other"}))

    def test_a_refuted_custom_copy_is_blocked_for_the_right_lead(self):
        gate = gv.GapGate(self.custom, self.research, self.report)
        v = gate.check({"id": "acme", "company": "Acme"})
        self.assertEqual(v["status"], gv.BLOCKED)
        self.assertEqual(gate.summary(), {"blocked": 1})

    def test_a_missing_report_blocks_a_confirmed_gap(self):
        os.remove(self.report)
        gate = gv.GapGate(self.custom, self.research, self.report)
        self.assertEqual(gate.check({"id": "acme", "company": "Acme"})["status"], gv.BLOCKED)


class MatchingTheBody(unittest.TestCase):
    """A draft already in the queue is found by its body, whatever its id says."""

    def setUp(self):
        import json
        import tempfile
        self.dir = tempfile.mkdtemp()
        self.custom = os.path.join(self.dir, "custom")
        os.makedirs(self.custom)
        self.body = ("Your docs describe a workflow's source as the set of URLs it extracts from. "
                     "That means a fund must hand-build a pipeline per publication.")
        for name in ("acme.json", "acme.touch2.json"):
            with open(os.path.join(self.custom, name), "w") as fh:
                json.dump({"subject": "s", "body": self.body}, fh)

    def test_the_first_touch_body_is_recognised(self):
        text = "Hi Wei,\n\n" + self.body + "\n\n[Name], Founder, NewsletterFIT"
        self.assertEqual(gv.custom_body_hit(self.custom, "acme", text), "acme")

    def test_a_per_touch_body_is_recognised(self):
        text = "Hi Wei,\n\n" + self.body
        self.assertEqual(gv.custom_body_hit(self.custom, "acme.touch2", text), "acme.touch2")

    def test_another_company_s_text_is_not_this_lead_s(self):
        text = "Hi Wei,\n\n" + self.body
        self.assertIsNone(gv.custom_body_hit(self.custom, "other", text))

    def test_a_lane_template_is_not_mistaken_for_researched_copy(self):
        text = ("Hi Wei,\n\nThree lists taking sponsors in your lane, most recently Data "
                "Engineering Central.\n\n- Data Engineering Central (est. 23K): [TRACKED_LINK]")
        self.assertIsNone(gv.custom_body_hit(self.custom, "acme", text))

    def test_a_short_body_is_not_evidence(self):
        with open(os.path.join(self.custom, "tiny.json"), "w") as fh:
            import json
            json.dump({"subject": "s", "body": "Newsletter mentions."}, fh)
        self.assertIsNone(gv.custom_body_hit(self.custom, "tiny", "Hi Wei,\n\nNewsletter mentions."))


class RetiredCopy(unittest.TestCase):
    """A withdrawn copy file is a record, not an input."""

    def setUp(self):
        import json
        import tempfile
        self.dir = tempfile.mkdtemp()
        self.custom = os.path.join(self.dir, "custom")
        os.makedirs(self.custom)
        body = ("Your sourcing is enumerated as ATS, job boards, open web and partner databases, "
                "which is a candidate-side inventory rather than an editorial one.")
        with open(os.path.join(self.custom, "hireez.retired.json"), "w") as fh:
            json.dump({"subject": "s", "body": body,
                       "retired": {"retired_at": "2026-10-22", "reason": "not a prospect"}}, fh)
        with open(os.path.join(self.custom, "crustdata.json"), "w") as fh:
            json.dump({"subject": "s", "body": "A sixth dataset beside companies, people and jobs."}, fh)
        self.body = body

    def test_a_retired_file_is_not_an_active_stem(self):
        self.assertEqual(gv.custom_stems(self.custom), {"crustdata"})

    def test_a_retired_body_is_not_matched_against_a_draft(self):
        text = "Hi Dubzee,\n\n" + self.body
        self.assertIsNone(gv.custom_body_hit(self.custom, "hireez", text))

    def test_the_retired_lead_has_no_custom_copy_to_draft(self):
        gate = gv.GapGate(self.custom, os.path.join(self.dir, "research"), os.path.join(self.dir, "r.json"))
        self.assertIsNone(gate.check({"id": "hireez", "company": "HireEZ"}))


class SendPath(unittest.TestCase):
    """The gate holds a queued draft whose researched body has lost its evidence."""

    def setUp(self):
        import json
        import tempfile
        import nf_send_gate as send_gate
        self.send_gate = send_gate
        self.dir = tempfile.mkdtemp()
        self.custom = os.path.join(self.dir, "custom")
        research = os.path.join(self.dir, "research")
        os.makedirs(self.custom)
        os.makedirs(research)
        self.body = ("Your docs describe a workflow's source as the set of URLs it extracts "
                     "from. That means a fund must hand-build a pipeline per publication.")
        with open(os.path.join(self.custom, "acme.json"), "w") as fh:
            json.dump({"subject": "A newsletter corpus for your fund customers", "body": self.body}, fh)
        with open(os.path.join(research, "acme.json"), "w") as fh:
            json.dump(record("confirmed"), fh)
        report = os.path.join(self.dir, "verify-report.json")
        with open(report, "w") as fh:
            json.dump({"Acme": entry([MISS])}, fh)
        send_gate.CUSTOM_DIR = self.custom
        send_gate._GAP_GATE = gv.GapGate(self.custom, research, report)
        token = "abcdefghijklmnopqrstuvwxyz012345"
        self.ctx = {
            "ledger": {token: {"dest": "https://newsletterfit.com/app/publications/x"}},
            "leads": {"acme": {"id": "acme", "company": "Acme", "contact_name": "Wei Zhang",
                               "email": "wei@acme.com", "domain": "acme.com", "stage": "leads",
                               "lead_source": "icp_research"}},
            "sent": {}, "replied": {},
            "by_company": {"acme": [{"id": "acme", "to": "wei@acme.com"}]},
            "corpus": type("C", (), {"placements": lambda s, c: [],
                                     "sponsor_count": lambda s, c: 0,
                                     "publication": lambda s, n: None,
                                     "recent_for": lambda s, c: []})(),
        }
        self.draft = {"id": "acme", "company": "Acme", "to": "wei@acme.com",
                      "from": "ian@newsletterfit.com",
                      "subject": "A newsletter corpus for your fund customers",
                      "text": "Hi Wei,\n\n" + self.body + "\n\n[Name], Founder, NewsletterFIT",
                      "html": f'<a href="https://newsletterfit.com/api/click?lt={token}">x</a>'}

    def test_a_blocked_researched_body_is_held_at_the_gate(self):
        verdict, findings = self.send_gate.gate(self.draft, self.ctx)
        messages = [f["message"] for f in findings if f["level"] == "HOLD"]
        self.assertEqual(verdict, "HOLD")
        self.assertTrue(any("researched copy" in m and "not backed" in m for m in messages), messages)

    def test_a_verified_body_is_not_held_for_its_research(self):
        import json
        report = os.path.join(self.dir, "verify-report.json")
        with open(report, "w") as fh:
            json.dump({"Acme": entry([OK])}, fh)
        self.send_gate._GAP_GATE = gv.GapGate(self.custom, os.path.join(self.dir, "research"), report)
        _, findings = self.send_gate.gate(self.draft, self.ctx)
        self.assertFalse(any("not backed" in f["message"] for f in findings))


class RealTree(unittest.TestCase):
    """The rescued 2026-10-21 audit, run through the gate as the seeder would."""

    def setUp(self):
        self.gap = os.path.join(REPO, "data", "icp-gap")
        if not os.path.isdir(self.gap):
            self.skipTest("no data/icp-gap in this checkout")
        self.gate = gv.GapGate(os.path.join(REPO, "data", "icp-custom"),
                               os.path.join(self.gap, "research"),
                               os.path.join(self.gap, "verify-report.json"))

    def status(self, lead_id, company):
        return self.gate.check({"id": lead_id, "company": company})["status"]

    def test_a_verified_gap_is_draftable(self):
        self.assertEqual(self.status("icypeas", "Icypeas"), gv.VERIFIED)
        self.assertEqual(self.status("kadoa", "Kadoa"), gv.VERIFIED)

    def test_the_copy_whose_own_evidence_refutes_it_is_blocked(self):
        # Common Room's research cites a docs row that says newsletter-subscribed contacts can be
        # imported. The quote is not on the rendered page, so the claim was never verified.
        self.assertEqual(self.status("common-room", "Common Room"), gv.BLOCKED)

    def test_an_unverified_gap_is_ask_only(self):
        self.assertEqual(self.status("orbifold-ai", "Orbifold AI"), gv.ASK_ONLY)


if __name__ == "__main__":
    unittest.main(verbosity=2)
