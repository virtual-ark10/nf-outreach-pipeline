---
name: outreach-attribution
description: "Tag emailed links to attribute leads to site visits."
version: 1.0.0
author: Nous Research
license: MIT
platforms: [linux]
---

# Outreach Lead-Visit Attribution

Determines which specific leads from an email campaign actually clicked and
visited the site, and which suggestion (link) they engaged with. This is the
layer GA4 + HubSpot/Mixmax do NOT give natively: GA4 stops at campaign-level
traffic; outreach tools stop at "contact X clicked." The missing link — mapping
a click back to a lead AND their repeat visits — is what this workflow builds.

## When to use
- User asks "can you tell whether the people I email are visiting my site?",
  or asks to tag outreach email links for tracking.
- Site is Astro + Express/Node, emails sent from Outlook (no tracking).
- Want to compare against off-the-shelf tools ("would you do this better than
  X? which tool should I use?").

## Honest framing to give the user first (don't build blind)
- Analytics (GA4) and click-tracking (HubSpot Free / Mixmax free tier) are
  SOLVED and free — do NOT rebuild them. The one thing worth building is the
  lead-level attribution layer.
- Spam-safety: emailed links live on the user's OWN domain and carry ONLY a
  random token — no external URL, no `dest=` param, no UTM by default. That
  pattern looks like ordinary click-tracking (HubSpot/Mailchimp-style) and
  avoids open-redirect heuristics in Gmail/Outlook/AV. Real deliverability is
  still driven by sending reputation (SPF/DKIM/DMARC, warm domain, engagement).
- Caveats: only leads who click a token link are attributed; anonymous visitors
  aren't linkable without a paid reverse-IP/company lookup. Email opens are
  unreliable (Apple Mail auto-loads pixels) — rely on clicks + visits.

## The reference implementation
A working, tested vanilla-JS (ES modules, zero-dep JSON store, no build) package
exists at `/home/boxed/newsletterfit/attribution`. Full command-level detail,
link shape, and sheet-sync instructions are in `references/nf-attribution.md`.
Key moving parts:
- `src/token.js` — `generateToken(leadId, campaign, linkRef, dest, ttlDays)`
  and `buildTokenLink(baseUrl, tok, utm)`. `linkRef` = which suggestion this
  link guards (e.g. `pub-migma`, `article-recap`); `dest` = where the click
  redirects (stored SERVER-SIDE, never in the email link).
- `src/store.js` — `AttributionStore`, JSON-file backed. To use Postgres later,
  reimplement its methods (`addToken/lookupToken/recordClick/recordVisit/
  visitsForLead/allVisits/summary`); the rest of the package is unchanged.
- `src/middleware.js` — `clickRouter(store, opts)` (`/api/click` endpoint) +
  `trackVisitMiddleware(store)` (site-wide page views).
- `utils/generate-links.js` — batch token-link generator from a leads CSV.
- `utils/export-visits.js` — per-lead `visits.csv` + `summary.csv`.
- `utils/sync-to-sheet.js` — merge attribution into the live tracking sheet.
- `examples/express-integration.js`, `test/smoke.js` (21 e2e checks).

## Generating tagged links (the core UX)
Input CSV per batch (`leads.csv`), header REQUIRED:
```
lead_id,campaign,dest1,ref1,dest2,ref2
lead_101,campaign-1,https://migma.io,pub-migma,https://techscoop.io/recap-2026,article-recap
```
```
node utils/generate-links.js leads.csv        # -> links.csv
node utils/generate-links.js --lead lead_101 --campaign c --dest https://x.io --ref pub-x
```
One leader row per suggested pub/article → multiple tracked links per lead, so a
click tells you WHICH suggestion. Paste the returned `link` URLs into the email
(Outlook auto-links pasted URLs).

links.csv is a per-run artifact: the generator overwrites it on EVERY run, so it
can hold fewer rows than the store. `attribution.json` (key `clicks`) is the
complete ledger of every token ever minted. To rebuild the batch CSV from the
store without minting new tokens:

```
node scripts/rebuild-links.js [campaign]     # token-only links (all campaigns if omitted)
```

## Export to a live tracking sheet
- READ is free even without OAuth (link-shared CSV export via
  `curl .../export?format=csv`).
- `sync-to-sheet.js` merges per-lead summary (matched by Company Name) into new
  columns: `Visited? | First Visit | Last Visit | Visit Count | Clicks`.
- WRITE into the sheet needs Google OAuth (google-workspace skill). If it's not
  set up, the exporter produces a ready-to-import CSV instead. Do NOT claim the
  sheet is updated until OAuth exists and the write returns success.

## Pitfalls
- JSON store `addToken` must normalize camelCase input (`leadId`, `expiresAt`)
  into snake_case rows (`lead_id`, `expires_at`) or lookups/export break — the
  token/`generateToken` and everything else read snake_case.
- native `better-sqlite3` needs a compile toolchain (node-gyp); if the box can't
  build it, switch to the zero-dep JSON store rather than fighting the build.
- The generator skips the first CSV line (header). A header-less CSV silently
  drops the first data row.
- Repeat-visit tracking only follows a lead onto YOUR domain (the attribution
  cookie is your domain); external-pub clicks count as a click but not as a
  tracked repeat visit.
- Write tooling to vanilla JS (ES modules) for a user who runs plain Node/JS —
  do NOT default to TypeScript or a framework.
- `links.csv` is overwritten on every generate-links run — it only holds the
  last run's rows. `attribution.json` (key `clicks`, NOT `tokens`) is the
  source of truth; regenerate with `scripts/rebuild-links.js [campaign]`.
  Never re-run the generator just to re-export: it mints FRESH tokens and
  orphans the ones already pasted into sent emails.
- Token generation ≠ deployed tracking. Minted links 404 until the live
  backend actually serves them. This session's full 'minted but 404' checklist
  (env gate, prod store path, boot-time store cache, restart order, probe
  pattern) is in `references/outreach-link-deploy.md` — read it before sending.
  Short version:
  - Routes are env-gated: app.js only mounts /api/click + /api/attribution/pv
    when `ENABLE_OUTREACH_ATTRIBUTION=true`. Check the flag AND the store
    before claiming 'not deployed'.
  - Live store = `/var/lib/newsletterfit/attribution.json` (newsletterfit
    owned, 640 — boxed cannot write/read; root/deploy-gate merge). The
    writable dev store is `/home/boxed/newsletterfit/attribution/attribution.json`.
  - `AttributionStore` caches the file ONCE at process boot. Tokens merged
    after the API started 404 with body "Unknown or expired link". Fix order:
    merge tokens into the prod store, THEN
    `systemctl restart newsletterfit-api.service`.
  - Probe: `scripts/verify-tracking-links.sh <token>...` → expect 302 +
    Location + `set-cookie: nf_attr=...`; pv beacon expects 204.
- ALL OUTREACH LINKS MUST BE INTERNAL (policy 2026-09-08): never send a lead
  to an external pub site — you lose the repeat-visit cookie and hand value
  away. Resolve each suggested pub to `https://newsletterfit.com/app/publications/<slug>`
  via the corpus search API (`GET $NEWSLETTERFIT_API/search?q=<pub name>`) and
  mint the token against THAT dest. Loop that does it automatically over the
  Pad drafts queue: `python3 /home/boxed/outreach_internalize.py` (reads
  drafts.json, resolves slugs, mints/reuses internal tokens, rewrites text+html,
  writes store + `attribution.new-tokens.json` merge payload).
- "Tokenize a link/URL" in this domain = MINT a tracking link
  (`https://.../api/click?lt=<32-hex>`). It never means counting LLM tokens —
  don't reach for tiktoken.