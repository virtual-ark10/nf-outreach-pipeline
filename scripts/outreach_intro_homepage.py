#!/usr/bin/env python3
"""Add NewsletterFIT intro paragraphs + per-lead homepage tracking links to first-contact drafts.

For every draft in the pad's LIVE queue (SQLite-backed since 2026-09-10, read and
written over the pad's API — the retired data/drafts.json is not the queue):
  1. Mints a per-lead tracking token for https://newsletterfit.com (homepage) via
     POST {NEWSLETTERFIT_API}/outreach/links — signature link is attributable to
     that lead.
  2. Replaces the thin lead-in sentences with a proper intro paragraph
     describing what NewsletterFIT is and does.
  3. Turns the signature's "newsletterfit.com" into an internal tracked anchor
     (html only; text stays plain as correct for text/plain).
  4. Wraps bullet tracking URLs in anchors (idempotent — skip already-anchored).
  5. Mirrors new tokens into the local attribution store + audit file.

Usage:
  python3 outreach_intro_homepage.py [--pad http://127.0.0.1:3001] [--store path] [--dry-run]
Reusing outreach_internalize.api_mint / wrap_links / load_env / read_live_queue.
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from outreach_internalize import (  # noqa: E402
    api_mint, load_env, read_live_queue, save_changed_drafts, wrap_links,
)

LT_RE = re.compile(r"https://newsletterfit\.com/api/click\?lt=([A-Za-z0-9_-]{20,40})")
SIG_TEXT = "Ian Hinga, Founder, NewsletterFIT — newsletterfit.com"
BASE_URL = "https://newsletterfit.com"

# (draft id, exact lead-in string to replace, replacement intro paragraph)
INTROS = {
    "xplor-pay": (
        "I track sponsorship behavior across 800K+ newsletters. Three pubs overlap with what you're already buying:",
        "First, a quick intro: NewsletterFIT tracks sponsorship behavior across 800K+ newsletters — which advertisers are buying placements, in which publications, and against which audiences. Three pubs overlap with what you're already buying:",
    ),
    "mcalvany-precious-metals": (
        "If that audience is working for you, the same signals show up in three others:",
        "Quick intro: NewsletterFIT is a sponsorship-intelligence platform. We track which advertisers appear in 800K+ newsletters, how often, and what those lists' audiences look like. If that audience is working for you, the same signals show up in three others:",
    ),
    "stacker": (
        "We track who's buying adjacent AI-search audiences:",
        "For context, NewsletterFIT is a sponsorship-intelligence platform: we monitor ad placements across 800K+ newsletters — who's buying, where, and at what cadence — so you can find lists your competitors have already validated. Here's who we see buying adjacent AI-search audiences:",
    ),
    "bolt-new": (
        "The same audience patterns show up here:",
        "For context, NewsletterFIT tracks sponsorship activity across 800K+ newsletters — which companies advertise where, how often, and against which audiences. The same audience patterns show up here:",
    ),
    "ground-news": (
        "Three similar news audiences we've flagged:",
        "Quick intro: NewsletterFIT is a sponsorship-intelligence product — we track who advertises in 800K+ newsletters, in which titles, and against which audiences. Three similar news audiences we've flagged:",
    ),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pad", default=os.environ.get("PAD_URL", "http://127.0.0.1:3001"),
                    help="the pad's base URL (its /api/drafts is the live queue)")
    ap.add_argument("--store", default="/home/boxed/newsletterfit/attribution/attribution.json")
    ap.add_argument("--out-tokens", default="/home/boxed/newsletterfit/attribution/attribution.homepage-tokens.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    env = load_env("/home/boxed/.config/newsletterfit/corpus.env")
    api_base = os.environ.get("NEWSLETTERFIT_API") or env.get("NEWSLETTERFIT_API", "http://127.0.0.1:3000")
    api_token = os.environ.get("API_BEARER_TOKEN") or env.get("API_BEARER_TOKEN", "")
    if not api_token:
        print("FATAL: API_BEARER_TOKEN not found", file=sys.stderr)
        sys.exit(1)

    drafts = read_live_queue(args.pad)
    before = {d.get("id"): (d.get("text"), d.get("html")) for d in drafts}
    store = json.load(open(args.store, encoding="utf-8"))
    clicks = store.get("clicks", [])

    # Existing homepage token per lead (dedupe reuse)
    by_lead_home = {}
    for c in clicks:
        if c.get("dest", "").rstrip("/") == BASE_URL:
            by_lead_home[c.get("lead_id")] = c

    new_tokens = []
    changed = 0
    for draft in drafts:
        lead_id = draft.get("id")
        if lead_id not in INTROS:
            print(f"  [SKIP] {lead_id}: no intro mapping")
            continue
        old_lead, new_intro = INTROS[lead_id]
        text = draft.get("text", "") or ""
        html = draft.get("html", "") or ""
        if old_lead not in text or old_lead not in html:
            print(f"  [WARN] {lead_id}: lead-in not found verbatim — draft may have been edited")
            continue

        # 1) homepage tracking token (per lead)
        home = by_lead_home.get(lead_id)
        if home is None and not args.dry_run:
            home = api_mint(api_base, api_token, lead_id, BASE_URL, "site-nf", "sep2-2026")
            clicks.append(home)
            by_lead_home[lead_id] = home
            new_tokens.append(home)
        if home is None:
            print(f"  [DRY] {lead_id}: would mint homepage token")
            continue
        home_link = f"{BASE_URL}/api/click?lt={home['token']}"

        # 2) intro paragraph (substring replace — works standalone or embedded)
        text = text.replace(old_lead, new_intro)
        html = html.replace(old_lead, new_intro)

        # 3) signature -> tracked anchor (html only)
        if SIG_TEXT in html:
            html = html.replace(
                SIG_TEXT,
                f"Ian Hinga, Founder, NewsletterFIT — <a href=\"{home_link}\">newsletterfit.com</a>",
            )

        # 4) wrap any bare bullet tracking links into anchors (idempotent)
        for m in LT_RE.findall(html):
            url = f"{BASE_URL}/api/click?lt={m}"
            if f'href="{url}"' not in html:
                html = wrap_links(html, url)

        draft["text"] = text
        draft["html"] = html
        changed += 1
        print(f"  [OK] {lead_id}: intro + homepage link {home['token'][:10]}…")

    if not args.dry_run:
        saved, failed = save_changed_drafts(args.pad, drafts, before)
        print(f"== pushed {saved} rewritten draft(s) to the pad" + (f", {failed} FAILED" if failed else ""))
        json.dump(store, open(args.store, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        if new_tokens:
            json.dump({"tokens": new_tokens,
                       "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       "note": "Per-lead homepage (newsletterfit.com) tracking tokens for email signatures."},
                      open(args.out_tokens, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"\n== {changed} drafts updated, {len(new_tokens)} homepage tokens minted ==")
    print(f"Out: {args.out_tokens}")


if __name__ == "__main__":
    main()