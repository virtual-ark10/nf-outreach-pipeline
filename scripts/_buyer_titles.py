#!/usr/bin/env python3
"""Is the contact a buyer of sponsorship intelligence?

The lanes return whoever sits at the domain, and a scrape returns whoever the site publishes.
An Office Manager or a Social Media Manager is a real person at a real company and the mail will
be read by nobody who can buy, so sending to them spends a send, a domain's reputation and the
lead itself. The rule: draft to a buyer, hold the rest until a buyer address is found.

    from _buyer_titles import buyer_verdict
    ok, why = buyer_verdict(title)     # -> (True, "") or (False, "intern")

Unknown or empty titles are treated as buyers: the address may be the founder's own, and a
missing title is not evidence of a bad contact. The lists are deliberately blunt and are ranked
by evidence: a non-buyer word wins over a buyer word ("Marketing Intern" is an intern), which is
why the negative check runs first.
"""
from __future__ import annotations

import re

# Never the buyer, whatever else the title says.
NON_BUYER = (
    "intern", "trainee", "apprentice", "assistant", "office manager", "office-manager",
    "administrative", "recruiter", "talent", "sourcer", "people partner", "hr ",
    "engineer", "developer", "designer", "scientist", "architect", "analyst",
    "support", "customer service", "helpdesk", "success manager", "trainer",
    "social media", "community manager", "accountant", "bookkeep", "controller",
    "marketing operations", "sales operations", "revops", "operations manager",
    "coordinator", "specialist", "student", "teacher", "researcher",
)

# The people who own a sponsorship or data budget, in the order the pipeline prefers them.
BUYER = (
    "gtm", "go-to-market", "go to market", "growth", "demand gen", "demand generation",
    "marketing", "cmo", "chief marketing", "brand", "content", "partnership",
    "revenue", "cro", "chief revenue", "commercial", "sales", "business development",
    "founder", "co-founder", "ceo", "chief executive", "owner", "president", "managing director",
    "head of", "vp ", "vice president", "director",
)


def buyer_verdict(title) -> tuple[bool, str]:
    """(is_buyer, reason). A missing title is not a reason to hold a lead."""
    t = re.sub(r"\s+", " ", str(title or "").strip().lower())
    if not t:
        return True, ""
    for bad in NON_BUYER:
        if bad in t:
            return False, bad.strip()
    for good in BUYER:
        if good in t:
            return True, ""
    return True, ""


if __name__ == "__main__":
    import sys
    for value in sys.argv[1:]:
        ok, why = buyer_verdict(value)
        print(f"{value!r:<50} {'BUYER' if ok else 'HOLD (' + why + ')'}")
