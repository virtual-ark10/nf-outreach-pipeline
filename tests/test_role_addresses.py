#!/usr/bin/env python3
"""A shared mailbox is not a contact, offline: the rule, and the gate that enforces it.

On 2026-10-15 the first-touch batch sent thirteen emails to role mailboxes (support@anam.ai,
help@bigfoot.com, hello@factor.ai, info@cakewallet.com, hi@bytebytego.com, media@f2.com,
customer.service@alphatauri.com, support@creditbanc.io) while the CRM held the buyer's own name
for nearly every one of them, because the site scrape stores whatever the site publishes
(info@/support@) and nothing downstream objected. Anam's helpdesk auto-acknowledged a first
touch within twenty seconds and the CRM filed it as a reply.

These cases pin both halves: the classifier must not fire on a real person's address, and the
gate must hold a draft addressed to a queue whichever stage drafted it.
"""
import os
import re
import sys
import unittest
from unittest import mock

NF_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(NF_REPO, "scripts"))

from _address_rules import is_role_address      # noqa: E402
import nf_send_gate as gate                     # noqa: E402

TOKEN = "bQ7fZ2mK4pRs9tVx1yA3"                  # 20 chars, the shape the mint produces
LINK = f'<a href="https://newsletterfit.com/api/click?lt={TOKEN}">x</a>'
TEXT = ("Hi Nick,\n\n"
        "I run NewsletterFIT. We track sponsorships across newsletters and watch which lists "
        "are rising.\n\n"
        "Ian Hinga, Founder, NewsletterFIT")


class TheRule(unittest.TestCase):
    def test_the_mailboxes_this_batch_sent_to_are_role_addresses(self):
        for addr in ("support@anam.ai", "help@bigfoot.com", "hello@factor.ai",
                     "info@cakewallet.com", "hi@bytebytego.com", "media@f2.com",
                     "customer.service@alphatauri.com", "support@creditbanc.io",
                     "team@articos.com", "contact@adkit.so", "info@curatorial.com"):
            self.assertTrue(is_role_address(addr), addr)

    def test_compound_and_suffixed_queues_still_match(self):
        for addr in ("support.team@x.com", "hello.info@x.com", "accounts2@x.com",
                     "marketing+europe@x.com", "no-reply@x.com", "donotreply@x.com",
                     "postmaster@x.com", "chat@orbifold.ai", "legal@zyte.com"):
            self.assertTrue(is_role_address(addr), addr)

    def test_a_person_s_address_is_never_refused(self):
        # Every one of these is a real stored contact, and none may be read as a queue.
        for addr in ("nic@anam.ai", "carissa@bigfoot.com", "forrest@factor.ai",
                     "henry@cakewallet.com", "alex@bytebytego.com", "tina@alphatauri.com",
                     "remi.russo@hinge.co", "kateleismer@briefcatch.com",
                     "ahoughtaling@eztexting.com", "lknapp@squarespace.com",
                     "himanshu@x.com", "hillary@x.com", "prasad@x.com", "cara@x.com",
                     "saleh@x.com", "informed@x.com", "carissa.chiu@x.com",
                     "jim.tan@airops.com", "oscar@framer.com", "sergio@x.com"):
            self.assertFalse(is_role_address(addr), addr)

    def test_an_empty_or_missing_address_is_not_a_role_address(self):
        # Nothing to hold on: the gate's own missing-address rule owns that case.
        for addr in ("", None, "no-at-sign", "   "):
            self.assertFalse(is_role_address(addr))


class Corpus:
    """The export stub. `counts` lets a case state a real count per company."""

    counts = {}

    def placements(self, company):
        return self.counts.get(company, 3)

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


class TheGateHoldsAQueueAddress(unittest.TestCase):
    def test_a_first_touch_to_the_company_s_own_support_box_is_held(self):
        # Anam: the address is on the lead's own domain, so the domain check alone passed it.
        verdict, findings = run(lead(company="Anam", id="anam", domain="anam.ai",
                                     contact_name="Nic Siegle", email="support@anam.ai"),
                                d=draft(to="support@anam.ai", id="anam--first-email",
                                        company="Anam", subject="Anam in the corpus"))
        self.assertEqual(verdict, "HOLD")
        self.assertIn("role mailbox", messages(findings))

    def test_the_buyer_s_own_address_still_passes(self):
        verdict, findings = run(lead(company="Anam", id="anam", domain="anam.ai",
                                     contact_name="Nic Siegle", email="nic@anam.ai"),
                                d=draft(to="nic@anam.ai", id="anam--first-email",
                                        company="Anam", subject="Anam in the corpus",
                                        text=TEXT.replace("Hi Nick,", "Hi Nic,")))
        self.assertEqual(verdict, "PASS", messages(findings))

    def test_the_hold_applies_on_a_follow_up_rung_too(self):
        # Claude's follow-up went to support@anthropic.com: a queue address is held whichever
        # stage drafted the mail, because the buyer never saw it either way.
        verdict, findings = run(lead(company="Claude", id="claude", domain="claude.com",
                                     contact_name="Claude Comeau", email="support@anthropic.com",
                                     stage="follow_up_1", first_contact_at="2026-10-01T07:00:00Z"),
                                d=draft(to="support@anthropic.com", id="claude--follow-up-1",
                                        company="Claude", subject="Claude in the corpus"))
        self.assertEqual(verdict, "HOLD")
        self.assertIn("role mailbox", messages(findings))

    def test_chat_and_legal_boxes_are_held_like_any_other_queue(self):
        # Two ICP leads had no address but a footer inbox: Orbifold publishes chat@ and Zyte
        # publishes legal@. Neither is a person, and the gate must not treat "not literally
        # support@" as an exception, because a shared inbox is read by whoever watches it.
        for to, company, domain, name in (
                ("chat@orbifold.ai", "Orbifold AI", "orbifold.ai", "Enoch Zhu"),
                ("legal@zyte.com", "Zyte", "zyte.com", "Daniel Cave")):
            with self.subTest(to=to):
                row = lead(company=company, id=company.lower().replace(" ", "-"),
                           domain=domain, contact_name=name, email=to)
                d = draft(to=to, id=f"{row['id']}--first-email", company=company,
                          subject=f"{company} in the corpus")
                verdict, findings = run(row, d=d)
                self.assertEqual(verdict, "HOLD", messages(findings))
                self.assertIn("role mailbox", messages(findings))


class TheCountTokenStandsAlone(unittest.TestCase):
    """F2's opener went out as "8 F8 placements": the claimed count (2) also matched the digit
    inside the company name, so the --fix-counts substitution rewrote both digits."""

    def tearDown(self):
        Corpus.counts = {}

    def test_a_digit_inside_the_company_name_is_not_rewritten(self):
        Corpus.counts = {"F2": 8}
        text = TEXT.replace("Hi Nick,", "Hi Joel,\n\n2 F2 placements in the newsletters I track.")
        verdict, findings = run(lead(company="F2", id="f2", domain="f2.com",
                                     contact_name="Joel Schiffer", email="joel@f2.com"),
                                d=draft(to="joel@f2.com", id="f2--first-email", company="F2",
                                        subject="F2 in the corpus", text=text))
        holds = [f for f in findings if f.get("kind") == "count"]
        self.assertEqual(len(holds), 1, messages(findings))
        # exactly the substitution the gate makes before it re-gates a held count
        fixed = re.sub(holds[0]["pattern"], str(holds[0]["real"]), text)
        self.assertIn("8 F2 placements", fixed)
        self.assertNotIn("F8", fixed)

    def test_a_correct_count_on_a_company_with_a_digit_passes(self):
        Corpus.counts = {"F2": 2}
        text = TEXT.replace("Hi Nick,", "Hi Joel,\n\n2 F2 placements in the newsletters I track.")
        verdict, findings = run(lead(company="F2", id="f2", domain="f2.com",
                                     contact_name="Joel Schiffer", email="joel@f2.com"),
                                d=draft(to="joel@f2.com", id="f2--first-email", company="F2",
                                        subject="F2 in the corpus", text=text))
        self.assertEqual(verdict, "PASS", messages(findings))

    def test_a_standalone_wrong_count_is_still_caught_with_a_token_boundary(self):
        text = TEXT.replace("Hi Nick,", "Hi Nick,\n\n9 Profound placements in the newsletters.")
        verdict, findings = run(lead(), d=draft(text=text))
        self.assertEqual(verdict, "HOLD")
        self.assertIn("the corpus logs 3", messages(findings))


if __name__ == "__main__":
    unittest.main(verbosity=2)
