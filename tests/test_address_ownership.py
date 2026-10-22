#!/usr/bin/env python3
"""May this address fill a lead's contact column? Only if it is on the lead's own host.

The role-mailbox rule (tests/test_role_addresses.py) catches support@ and info@. It does not
catch the other half of the 2026-10-15 batch: namesake provider records. The free people lane
was asked about alphasense.com and returned a person whose address was
sensors.alphasense@ametek.com; the name pass wrote it into the CRM, a draft greeted that person,
and the mailbox belonged to a different company. The gate reviews it, but the CRM should never
have held it in the first place.
"""
import os
import sys
import unittest

NF_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(NF_REPO, "scripts"))

from _address_rules import address_domain, belongs_to_domain     # noqa: E402


class TheAddressMustSitOnTheLeadSHost(unittest.TestCase):
    def test_the_ametek_namesake_is_refused(self):
        # The live miss: AlphaSense's lead domain is alphasense.com, the address was AMETEK's.
        self.assertFalse(belongs_to_domain("sensors.alphasense@ametek.com", "alphasense.com"))

    def test_grant_thornton_for_an_aromatherapist_is_refused(self):
        self.assertFalse(belongs_to_domain("cstorrs@grantthornton.com", "aromatherapist.com"))

    def test_the_lead_s_own_address_passes(self):
        self.assertTrue(belongs_to_domain("niel@80000hours.org", "80000hours.org"))

    def test_case_and_www_do_not_matter(self):
        self.assertTrue(belongs_to_domain("Niel@80000Hours.org", "www.80000hours.org"))
        self.assertTrue(belongs_to_domain("niel@www.80000hours.org", "80000hours.org"))

    def test_a_subdomain_address_is_the_same_company(self):
        # AWS is aws.amazon.com; its people write from mail.amazon.com.
        self.assertTrue(belongs_to_domain("someone@mail.amazon.com", "amazon.com"))

    def test_a_subdomain_lead_accepts_its_parent(self):
        self.assertTrue(belongs_to_domain("someone@amazon.com", "aws.amazon.com"))

    def test_a_free_mail_address_never_matches(self):
        self.assertFalse(belongs_to_domain("delta.mu.alpha@gmail.com", "ashby.io"))

    def test_a_url_shaped_domain_is_understood(self):
        self.assertTrue(belongs_to_domain("x@tryprofound.com", "https://www.tryprofound.com/team"))

    def test_no_domain_on_the_lead_refuses_the_address(self):
        self.assertFalse(belongs_to_domain("someone@example.com", ""))
        self.assertFalse(belongs_to_domain("someone@example.com", None))

    def test_a_name_collision_on_a_shared_tld_is_not_a_match(self):
        # profound.tech is not profound.nl, and stata.com is not statistics.com.
        self.assertFalse(belongs_to_domain("a@profound.nl", "profound.tech"))
        self.assertFalse(belongs_to_domain("a@statistics.com", "stata.com"))

    def test_garbage_is_refused_rather_than_guessed(self):
        self.assertFalse(belongs_to_domain("", "example.com"))
        self.assertFalse(belongs_to_domain("not-an-address", "example.com"))
        self.assertEqual(address_domain("A@B.example.COM"), "b.example.com")


if __name__ == "__main__":
    unittest.main(verbosity=2)
