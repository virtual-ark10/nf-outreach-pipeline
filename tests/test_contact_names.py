#!/usr/bin/env python3
"""The free name pass, offline: who it is willing to greet a sponsor with.

The pass exists because the routed people search NF used to call sends a business-owner title
filter (owner / president / general manager / office manager) and so came back empty for the
GTM, growth and marketing people this pipeline sells to. These cases pin the two halves of the
fix: the title set that decides which person is chosen, and the guards that stop a namesake's
employee, an obfuscated initial or a junior from filling a contact.
"""
import os
import sys
import unittest

NF_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(NF_REPO, "scripts"))

import fill_contact_names as fcn      # noqa: E402


class RoleOrder(unittest.TestCase):
    def test_founder_rule_is_gtm_then_growth(self):
        roles = [fcn.role_of(t) for t in (
            "Founding GTM", "Head of Growth", "Creator Partnerships Manager",
            "VP of Marketing", "Chief Revenue Officer", "Co-Founder", "Bookkeeper")]
        self.assertEqual(roles, ["gtm", "growth", "partnerships", "marketing", "cro",
                                 "founder", "other"])

    def test_gtm_outranks_growth_and_founder(self):
        self.assertEqual(fcn.ROLE_ORDER.index("gtm"), 0)
        self.assertLess(fcn.ROLE_ORDER.index("growth"), fcn.ROLE_ORDER.index("founder"))

    def test_the_old_title_filter_is_not_the_one_we_use(self):
        """The borrowed filter asked for owners only; a growth manager must survive it."""
        for title in ("Director of Growth", "Growth Marketing Manager", "Founding GTM",
                      "VP of Marketing"):
            self.assertEqual(fcn.role_of(title) != "other", True, title)


class Guards(unittest.TestCase):
    def _p(self, name, title="", company="", domain=""):
        return {"name": name, "title": title, "company": company, "domain": domain,
                "role": fcn.role_of(title), "email": ""}

    def test_an_initial_is_not_a_name(self):
        self.assertFalse(fcn.usable(self._p("J. Smith")))

    def test_an_emoji_is_not_a_surname(self):
        self.assertFalse(fcn.usable(self._p("Rokas 🚀")))

    def test_a_lowercase_name_is_tidied(self):
        p = self._p("hai liu", "Head of Service Management")
        self.assertTrue(fcn.usable(p))
        self.assertEqual(p["name"], "Hai Liu")

    def test_mcdonald_is_left_alone(self):
        p = self._p("Ronald McDonald", "Owner")
        self.assertTrue(fcn.usable(p))
        self.assertEqual(p["name"], "Ronald McDonald")

    def test_a_junior_is_never_the_greeting(self):
        self.assertFalse(fcn.worth_writing(self._p("A B", "Junior Software Developer")))
        self.assertFalse(fcn.worth_writing(self._p("A B", "Marketing Intern")))
        self.assertFalse(fcn.worth_writing(self._p("A B", "Bookkeeper")))

    def test_a_marketer_is(self):
        self.assertTrue(fcn.worth_writing(self._p("A B", "Product Marketing Manager")))

    def test_a_senior_title_outside_the_role_set_is_kept(self):
        self.assertTrue(fcn.worth_writing(self._p("A B", "Director of Technology Infrastructure")))

    def test_a_namesake_employee_is_refused(self):
        # The corpus holds Stata, Delta, Zone, F2 and Profound: a namesake's staff must not be
        # greeted as the lead. The host the provider names is the evidence.
        self.assertFalse(fcn.brand_match("Profound", "profound.tech",
                                         "Profound Monitoring BV", "profound.nl"))
        self.assertFalse(fcn.brand_match("Stata", "stata.com", "Statistics.com", "statistics.com"))
        self.assertTrue(fcn.brand_match("Ultimus", "ultimus.com", "Ultimus", "ultimus.com"))
        self.assertTrue(fcn.brand_match("Zedcrest Wealth", "zedcrestwealth.com",
                                        "Zedcrest Investment Managers"))
        # No host to check: brand words are all there is, so a namesake of the same word passes.
        # That is why the first-touch gate re-checks the address domain at send time.
        self.assertTrue(fcn.brand_match("Profound", "profound.tech", "Profound Monitoring BV"))

    def test_a_role_mailbox_is_not_an_address(self):
        rows = fcn.people_of({"data": [{"first_name": "Jane", "last_name": "Doe",
                                        "title": "Head of Growth", "company_name": "Acme",
                                        "email": "info@acme.com"}]})
        self.assertEqual(rows[0]["email"], "")

    def test_a_persons_address_survives(self):
        rows = fcn.people_of({"data": [{"first_name": "Jane", "last_name": "Doe",
                                        "title": "Head of Growth", "company_name": "Acme",
                                        "email": "Jane.Doe@acme.com"}]})
        self.assertEqual(rows[0]["email"], "jane.doe@acme.com")


if __name__ == "__main__":
    unittest.main()
