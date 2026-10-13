#!/usr/bin/env python3
"""The gate's identity rules, offline: which address may a first touch actually go to?

A corpus lead's sponsor export row is matched by company name. That holds while the lead
carries a domain, and breaks when it does not: a lead named "Profound" was matched to the AI
search platform's export row and addressed to almar@profound.nl, a Dutch monitoring-equipment
maker that shares the name, and took two touches meant for the platform. These cases pin the
guard that stops that, and pin the history key that stopped a name collision from inheriting
another company's sends.

The corpus and the ICP size lane are stubbed, because neither is what is under test here.
"""
import os
import sys
import unittest
from unittest import mock

NF_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(NF_REPO, "scripts"))

import nf_send_gate as gate      # noqa: E402

TOKEN = "bQ7fZ2mK4pRs9tVx1yA3"          # 20 chars, the shape the mint produces
LINK = f'<a href="https://newsletterfit.com/api/click?lt={TOKEN}">x</a>'
TEXT = ("Hi Nick,\n\n"
        "I run NewsletterFIT. We track sponsorships across newsletters and watch which lists "
        "are rising.\n\n"
        "Ian Hinga, Founder, NewsletterFIT")


class Corpus:
    """The export stub: a grounded count, and no publication record to dispute."""

    def placements(self, company):
        return 3

    def publication(self, name):
        return None


def lead(**over):
    row = {"id": "profound", "company": "Profound", "domain": "tryprofound.com",
           "website": None, "contact_name": "Nick Lafferty", "email": "nick@tryprofound.com",
           "lead_source": "corpus", "stage": "leads", "first_contact_at": None,
           "unsubscribed": 0, "bounced": 0, "priority": "HIGH"}
    row.update(over)
    return row


def draft(to="nick@tryprofound.com", **over):
    d = {"id": "profound--first-email", "company": "Profound", "from": "ian@newsletterfit.com",
         "to": to, "subject": "Profound in the corpus", "text": TEXT, "html": f"<p>{LINK}</p>"}
    d.update(over)
    return d


def run(row, sent=None, replied=None, d=None):
    d = d or draft()
    ctx = {"ledger": {TOKEN: {"dest": "https://newsletterfit.com/api/click"}},
           "leads": {str(row["company"]).lower(): row} if row else {},
           "sent": sent or {}, "replied": replied or {},
           "by_company": {str(d["company"]).lower(): [d]}, "corpus": Corpus()}
    with mock.patch.object(gate.icp_fit, "verdict", lambda *a, **k: (True, "in-band", "stub")):
        return gate.gate(d, ctx)


def messages(findings):
    return " | ".join(f["message"] for f in findings)


class AddressMustBelongToTheCompany(unittest.TestCase):
    def test_no_recorded_domain_holds_a_first_touch(self):
        # The Profound case: nothing on the lead to match the address against.
        verdict, findings = run(lead(domain=None, website=None))
        self.assertEqual(verdict, "HOLD")
        self.assertIn("no domain recorded", messages(findings))

    def test_a_recorded_domain_with_its_own_address_passes(self):
        verdict, findings = run(lead())
        self.assertEqual(verdict, "PASS", messages(findings))

    def test_a_subdomain_of_the_recorded_domain_passes(self):
        # Only the domain rule is in play here, so the CRM's verified address is the same one.
        verdict, findings = run(lead(domain="tryprofound.com", email="nick@mail.tryprofound.com"),
                                d=draft(to="nick@mail.tryprofound.com"))
        self.assertEqual(verdict, "PASS", messages(findings))

    def test_a_website_url_counts_as_the_recorded_domain(self):
        verdict, findings = run(lead(domain=None, website="https://www.tryprofound.com/team"),
                                d=draft(to="nick@tryprofound.com"))
        self.assertEqual(verdict, "PASS", messages(findings))

    def test_a_free_mail_address_is_reviewed(self):
        # Ashby's CRM address is a gmail one, which can never match the export row.
        verdict, findings = run(lead(domain="ashby.io"), d=draft(to="delta.mu.alpha@gmail.com"))
        self.assertEqual(verdict, "REVIEW")
        self.assertIn("gmail.com", messages(findings))

    def test_an_address_on_another_domain_is_reviewed(self):
        # Archera: the lead's recorded domain is archera.io, the address is on archera.ai.
        verdict, findings = run(lead(domain="archera.io"), d=draft(to="someone@archera.ai"))
        self.assertEqual(verdict, "REVIEW")
        self.assertIn("archera.ai", messages(findings))

    def test_a_contacted_lead_is_not_re_judged(self):
        # A conversation in flight keeps its rung, the way the off-ICP rule leaves it alone.
        verdict, findings = run(lead(domain=None, website=None, stage="follow_up_2",
                                     first_contact_at="2026-10-01T07:00:00Z"))
        self.assertNotIn("no domain recorded", messages(findings))


class HistoryBelongsToTheLeadNotTheName(unittest.TestCase):
    def test_a_name_collision_does_not_inherit_another_lead_s_sends(self):
        # An unrelated lead could carry the id "profound"; this draft's lead is "profound-ai".
        sent = {"profound": ["Spotted Profound in #SEOForLunch"]}
        verdict, findings = run(lead(id="profound-ai"), sent=sent)
        self.assertEqual(verdict, "PASS", messages(findings))

    def test_the_lead_s_own_history_still_holds_a_first_touch(self):
        sent = {"profound": ["Spotted Profound in #SEOForLunch"]}
        verdict, findings = run(lead(id="profound"), sent=sent)
        self.assertEqual(verdict, "HOLD")
        self.assertIn("already emailed in this campaign", messages(findings))

    def test_a_reply_on_the_lead_s_own_id_still_holds(self):
        replied = {"profound": [("positive", "interested")]}
        verdict, findings = run(lead(id="profound"), replied=replied)
        self.assertEqual(verdict, "HOLD")
        self.assertIn("replied already", messages(findings))


if __name__ == "__main__":
    unittest.main(verbosity=2)
