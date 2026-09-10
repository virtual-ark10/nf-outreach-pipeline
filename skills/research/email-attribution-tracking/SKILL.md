---
name: email-attribution-tracking
description: "Track if emailed leads visit your site via token links."
version: 1.0.0
author: Nous Research
license: MIT
platforms: [linux]
---

# Email Lead-Visit Attribution Tracking

Answers the marketing question: **"which leads I emailed actually visited my site, and how
often?"** This is the layer no off-the-shelf tool natively fills. GA4 / HubSpot Frees /
Mixmax stop at "campaign drove X traffic" / "a contact clicked a link". None connect a
click back to a SPECIFIC lead and track their REPEAT visits over the following days. That
last link is worth building ourselves — it's small, self-hosted, and free.

## When to use
- "Can you track traffic on our site / where it comes from?"
- "Do I know if the leads we emailed are visiting the website?"
- Any request for lead-to-visit attribution, click tracking in emails, or a free
  outreach/email-tracking tool recommendation.

## Free tool stack (Outlook-based outreach)
- **HubSpot Free Sales Hub** — best all-round. Free forever, Outlook add-in, open+click
  tracking on the contact timeline, basic pipeline.
- **Mixmax** — free tier, Outlook-compatible, more polished sender UX; free plan more limited.
- **Instantly / Lemlist** — bulk cold outreach, but PAID (trial-only free). Revisit only if
  volume limits hit.
- **GA4** — traffic + UTM + referrer layer (usually already present; don't rebuild it).
- Trustworthy signal = a click + a site visit. Email OPENS are unreliable (Apple Mail
  auto-loads tracking pixels) — never build attribution on opens.

## The self-hosted attribution pattern
Reference build (NewsletterFIT, Astro + Express/Node, outreach via Outlook):
`/home/boxed/newsletterfit/attribution/` (src/, utils/, test/, README.md).
Full session detail in the reference:
  references/lead-visit-attribution.md
For a fresh build, the four pieces are:

1. **Per-lead token link** — each emailed lead gets a unique token baked into their link:
   gen token → store mapping {token → leadId, campaign}, build
   `https://site/api/click?lt=<TOKEN>&dest=%2Fpricing&utm_source=...&utm_campaign=...`
2. **Click endpoint** (`clickRouter`) — looks up token (which lead+campaign), records first
   click, sets attribution cookie (`HttpOnly, SameSite=Lax, ~30d Max-Age`), then **302-redirects
   to the real destination WITH the UTM params carried through** so GA4 still credits the campaign.
3. **Site-wide visit middleware** — on page views, if the attribution cookie is present, log
   that lead + campaign + path + referrer as a visit. This is what turns "clicked once" into
   "actually browsing over several days".
4. **Export / sheet sync** — emit per-lead summary (visit_count, first/last visit, clicked) to
   fold into the lead-tracking spreadsheet; keep campaign-level credit via UTM in GA4.

## Out-of-scope / honest limits (state up front)
- Attributes only leads who click a token link. Anonymous visitors who never click an email
  can't be tied to a lead without a PAID reverse-IP company lookup (Clearbit-type).
- Cookie-based: clearing cookies severs repeat-visit tracking (first click still captured).
- Reassure users, don't oversell: this nails "did my emailed leads visit," not
  "who is every anonymous visitor."

## Before you build: Resend-native tracking is not a substitute for this

The sending provider (Resend) ships open tracking (1x1 pixel), click tracking
(link rewriting through a tracking subdomain) and FREE custom tracking domains
(CNAME), with `email.opened` / `email.clicked` webhooks. Users reasonably ask
whether that replaces the token minting below. It is genuinely easier — no minting
service, no token store, no redirect endpoint, no token-format migration: one CNAME,
two flags, one webhook subscription. But it answers a DIFFERENT question, and the
difference is the whole point of this skill:

- Resend tells you "this email was opened / a link was clicked". It cannot say
  WHICH lead clicked, and it loses sight the moment the recipient lands on the site —
  exactly the repeat-visit and post-click behaviour the token + site middleware
  pattern exists to capture. Keep tokens authoritative for lead attribution.
- Opens are noise (Apple Mail prefetch inflates them, image blocking deflates them,
  scanners cache them). Resend's own docs advise tracking opens only on Broadcasts,
  never on transactional/1:1 mail, so inbox providers don't reclassify it as
  marketing — i.e. adding a pixel to cold outreach buys deliverability risk for a
  metric you should not act on.
- Trust hinges on the tracking domain: branded subdomain = fine; shared/third-party
  redirect = looks like tracking to recipients and to corporate URL scanners
  (SafeLinks, Proofpoint), which also manufacture phantom clicks.
- Sane end state is HYBRID: branded click tracking as a redundant feed/cross-check,
  token links still the source of truth.

Setup steps, API/plan facts, event names and the exact trade-off table:
`references/resend-native-tracking-vs-minting.md`.

## Pitfalls
- **Native-compile deps often fail on this server.** `better-sqlite3` needs node-gyp/build
  tooling that isn't installed (npm install fails). For drop-in Node packages here, prefer a
  **zero-dependency store** — e.g. a JSON-file store with atomic tmp+rename write. If the user
  later wants Postgres, reimplement the documented method set
  (addToken/lookupToken/recordClick/recordVisit/visitsForLead/summary) and swap it in.
- **CamelCase vs snake_case field drift.** Generate/API code often returns camelCase
  (`leadId`, `expiresAt`) while store/export/middleware read snake_case (`lead_id`,
  `expires_at`). Normalize ONCE at the write boundary (e.g. in `addToken`) so all readers
  agree. Un-normalized mismatch caused a confusing "token resolves null" bug caught only by
  a smoke test.
- **Always ship a smoke test** that drives the full flow (issue token → click → cookie →
  repeat visits → summary) against a temp store, and re-run it after edits.

## Verification
End-to-end check: generate a link, simulate a click (assert 302 redirect + cookie set),
simulate 2-3 cookie-bearing page views, assert summary shows visit_count and clicked.