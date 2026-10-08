#!/usr/bin/env python3
"""Lead quality preference for the NewsletterFIT outreach pipeline.

Founder rule (2026-10-08): MEDIUM sponsors convert better than the HIGH/top tier, so
enrichment and the send queue work MEDIUM leads first. The order lives here alone, and every
ranking site imports quality_rank() from it, so the rule cannot drift between the contact
finder, the Hunter enricher, the draft seeder and the send gate.

To flip the pipeline back, reorder PREFERENCE.
"""

# Best first. Anything not listed sorts after everything that is.
PREFERENCE = ("MEDIUM", "HIGH")

# Keys a lead row may carry its tier under. The CRM column is "priority"; the leads engine
# serves it to the API under the alias "quality" (pad/leads/server.cjs maps quality:'priority'),
# so an API-shaped dict has "quality" and a direct sqlite row dict has "priority". The corpus
# export calls the same tier "outreachQuality". quality_rank() reads all three so callers do
# not have to know which shape they are holding.
_TIER_KEYS = ("quality", "priority", "outreachQuality")


def quality_rank(lead) -> int:
    """Sort key position for a lead's tier. 0 = work first.

    Accepts a lead dict or a bare tier string. Unlisted tiers (LOW, blank, unknown) sort
    after everything in PREFERENCE, so a lead with no tier is never promoted ahead of a
    MEDIUM one by accident.
    """
    if isinstance(lead, dict):
        tier = ""
        for key in _TIER_KEYS:
            tier = str(lead.get(key) or "").strip()
            if tier:
                break
    else:
        tier = str(lead or "").strip()
    try:
        return PREFERENCE.index(tier.upper())
    except ValueError:
        return len(PREFERENCE)


def tier_of(lead) -> str:
    """The tier string quality_rank() read, for logging."""
    if isinstance(lead, dict):
        for key in _TIER_KEYS:
            tier = str(lead.get(key) or "").strip()
            if tier:
                return tier.upper()
        return ""
    return str(lead or "").strip().upper()
