#!/usr/bin/env python3
"""Placement-count parsing, offline: the gate must read the number a reader sees.

A draft for 80,000 Hours said "1 80,000 Hours placements" and the gate held it as a claim of
0: the regex found the bare "000" after the comma sitting at a token boundary. The same
token-boundary rule exists to stop a digit INSIDE a company name (F2, F8) from being read as
the count, so the fix has to keep that guard and add the thousands separator to it. These
cases pin both directions, plus the substitution pattern that --fix-counts rewrites with.
"""
import os
import re
import sys
import unittest
from unittest import mock

NF_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(NF_REPO, "scripts"))

import nf_send_gate as gate      # noqa: E402

TOKEN = "bQ7fZ2mK4pRs9tVx1yA3"          # 20 chars, the shape the mint produces
LINK = f'<a href="https://newsletterfit.com/api/click?lt={TOKEN}">x</a>'


class Corpus:
    """One grounded placement for 80,000 Hours, no publication record to dispute."""

    def placements(self, company):
        return 1

    def publication(self, name):
        return None


def lead(**over):
    row = {"id": "80-000-hours", "company": "80,000 Hours", "domain": "80000hours.org",
           "website": "https://80000hours.org", "contact_name": "Niel Bowerman",
           "email": "niel@80000hours.org", "lead_source": "corpus", "stage": "leads",
           "first_contact_at": None, "unsubscribed": 0, "bounced": 0, "priority": "MEDIUM"}
    row.update(over)
    return row


def text(claim_line):
    return (f"Hi Niel,\n\n{claim_line}\n\n"
            "I'm building NewsletterFIT: a corpus of newsletters that reads who sponsors whom "
            "and which pubs are rising.\n\n"
            "One list worth a look:\n\n- AI as Normal Technology (est. 88K)\n\n"
            "Ian Hinga, Founder, NewsletterFIT")


def run(claim_line, row=None, html=None):
    row = row or lead()
    d = {"id": "80-000-hours", "company": row["company"], "from": "ian@newsletterfit.com",
         "to": row["email"], "subject": "Spotted 80,000 Hours in Understanding AI",
         "text": text(claim_line),
         "html": html or (f'<p>- <a href="https://newsletterfit.com/api/click?lt={TOKEN}">'
                          'AI as Normal Technology</a> (est. 88K)</p>')}
    ctx = {"ledger": {TOKEN: {"dest": "https://newsletterfit.com/app/publications/ai-as-normal-technology"}},
           "leads": {str(row["company"]).lower(): row},
           "sent": {}, "replied": {},
           "by_company": {str(row["company"]).lower(): [d]}, "corpus": Corpus()}
    with mock.patch.object(gate.icp_fit, "verdict", lambda *a, **k: (True, "in-band", "stub")):
        return gate.gate(d, ctx)


def messages(findings):
    return " | ".join(f["message"] for f in findings)


class ThousandsSeparator(unittest.TestCase):
    def test_the_separator_does_not_become_the_claim(self):
        self.assertEqual(gate.claimed_placement_counts("1 80,000 Hours placements, x"), [1])

    def test_the_real_claim_is_still_read(self):
        self.assertEqual(gate.claimed_placement_counts("12 80,000 Hours placements"), [12])

    def test_a_digit_inside_a_company_name_is_not_a_claim(self):
        # "2 F2 placements" is the shape the old lookbehind was written for.
        self.assertEqual(gate.claimed_placement_counts("2 F2 placements"), [2])
        self.assertEqual(gate.claimed_placement_counts("placements in F2"), [])

    def test_a_bare_count_still_matches(self):
        self.assertEqual(gate.claimed_placement_counts("3 placements in 20VC"), [3])

    def test_the_claim_matches_the_corpus(self):
        verdict, findings = run("1 80,000 Hours placements in the newsletters I track, "
                                "across Understanding AI.")
        self.assertEqual(verdict, "PASS", messages(findings))

    def test_a_wrong_count_is_still_a_hard_hold(self):
        verdict, findings = run("0 80,000 Hours placements in the newsletters I track.")
        self.assertEqual(verdict, "HOLD", messages(findings))
        self.assertIn("the corpus logs 1", messages(findings))

    def test_the_substitution_pattern_rewrites_the_count_not_the_separator(self):
        verdict, findings = run("2 80,000 Hours placements in the newsletters I track.")
        hold = next(f for f in findings if f.get("kind") == "count")
        fixed = re.sub(hold["pattern"], str(hold["real"]),
                       "2 80,000 Hours placements in the newsletters I track.")
        self.assertEqual(fixed, "1 80,000 Hours placements in the newsletters I track.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
