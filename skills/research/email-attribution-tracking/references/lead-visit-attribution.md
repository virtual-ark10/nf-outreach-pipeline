# Lead-Visit Attribution — NewsletterFIT build detail (2026-08-28)

Concrete session for `email-attribution-tracking`. Prebuilt, tested package:
`/home/boxed/newsletterfit/attribution/` (src/, utils/, test/, examples/, README.md).
Site: newsletterfit.com — Astro + Express/Node backend, outreach via Outlook, GA4 already in place.

## Product decision context
- User's goal: "I want to know if the leads we email are visiting the website." Lead list =
  the sponsor outreach targets in the lead-tracking Google Sheet.
- Keep it free: user strongly prefers free tiers (declined paying for X/Brave). HubSpot Free
  Sales Hub recommended over Instantly/Lemlist (paid). No SaaS per-seat attribution tool.
- Honesty framing that landed well: GA4 is fine at traffic level; outreach tools are fine at
  click level; the missing + worth-building piece is lead-level repeat-visit attribution.
  Explicitly stated limits (only emailed leads, paid reverse-IP for anonymous) so we don't oversell.

## Verified test output (all 15 smoke checks passed)
Token link format:
  https://newsletterfit.com/api/click?lt=<TOKEN36hex>&dest=%2Fpricing&utm_source=outreach&utm_medium=email&utm_campaign=campaign-1
- token resolves to lead → click 302-redirects to dest, sets `nf_lead` cookie, records first_click_at.
- click alone does NOT count as a visit; only cookie-bearing page views do.
- 3 page views w/ cookie → summary shows lead_id, visit_count=3, clicked=1.
- unknown token → null; expired token (ttlDays<0) → null.

## Package layout / methods
- src/token.js: generateToken(leadId, campaign, ttlDays) → {token,leadId,campaign,createdAt,expiresAt};
  buildTokenLink(baseUrl,token,dest,utm); resolveToken(store,token) null if unknown/expired.
- src/middleware.js: parseCookies; clickRouter(store,{cookieMaxDays,cookieDomain}) →
  Express handler at /api/click; trackVisitMiddleware(store) → site-wide page-view logger.
- src/store.js: AttributionStore(filePath) JSON-file store. Methods:
  addToken / lookupToken / recordClick / recordVisit / visitsForLead / allVisits / summary.
- utils/generate-links.js: reads leads.csv (lead_id,campaign) → links.csv + saves tokens.
  Env NF_BASE_URL (default https://newsletterfit.com), NF_ATTRIBUTION_DB.
- utils/export-visits.js: --csv out → visits.csv + summary.csv. Env NF_ATTRIBUTION_DB.
- examples/express-integration.js: 3-line wire-in + optional /api/lead-visits?leadId= endpoint.

## Pitfalls hit during build (latent, re-affirmed)
1. better-sqlite3 npm install FAILED (node-gyp native compile; no build toolchain on this
   box). Switched to a zero-dep JSON-file store (atomic tmp+rename write). Lesson: for
   drop-in Node work here, don't reach for native deps first.
2. Field-drift bug: generateToken returned camelCase (leadId, createdAt, expiresAt) but
   store/export/middleware read snake_case (lead_id, created_at, expires_at). Mismatch →
   resolveToken returned a row missing expires_at → Date.parse(undefined)=NaN → comparison
   false → "token resolves null" smoke failure. Fix: normalize once in addToken into
   snake_case rows. Only caught by the smoke test — always run it after edits.

## Optional follow-up offered to user (not yet done)
Auto-push per-lead visit columns (Visited?, First Visit, Last Visit, Visit Count) from
summary.csv into the live lead sheet via the Sheets API — offer, don't build unprompted.
Sheet ID: 1wB0xxihPy4usJnVZHuN0W31rr26j05aZnL6ymzgbWLk.