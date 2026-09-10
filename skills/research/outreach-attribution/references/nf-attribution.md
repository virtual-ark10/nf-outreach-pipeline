# NewsletterFIT click/visit attribution (nf-attribution)

Lead-level attribution: which emailed lead clicked WHICH suggestion, and did
they visit the site + how often. All free, plain vanilla JS (ES modules,
zero-dependency JSON store, no build). Package lives at
`/home/boxed/newsletterfit/attribution`.

## Files
- `src/token.js` — `generateToken(leadId, campaign, linkRef, dest, ttlDays)` +
  `buildTokenLink(baseUrl, tok, utm)`. `linkRef` = which suggestion (e.g.
  `pub-migma`); `dest` = where the click lands (stored SERVER-SIDE).
- `src/store.js` — `AttributionStore`: JSON-file backed (`attribution.json`),
  tables `clicks` + `visits`. Methods: `addToken / lookupToken / recordClick /
  recordVisit / visitsForLead / allVisits / summary`. To use Postgres later,
  reimplement these methods; the rest of the package is unchanged.
- `src/middleware.js` — `clickRouter(store, opts)` (the `/api/click` endpoint)
  + `trackVisitMiddleware(store)` (site-wide page views).
- `utils/generate-links.js` — batch token-link generator for a lead list.
- `utils/export-visits.js` — per-lead `visits.csv` + `summary.csv`.
- `utils/sync-to-sheet.js` — merges attribution into the live lead sheet.
- `examples/express-integration.js` — how to mount in an Astro/Express backend.
- `test/smoke.js` — 21 e2e checks (`node test/smoke.js`).

## Link shape (show the user exactly this)
```
https://newsletterfit.com/api/click?lt=<32-hex-token>
```
`lt` is a random token only the server maps to {leadId, linkRef}. Links carry
ONLY the token — no UTM, no `dest=`, no external URL (keeps emailed links
short: `buildTokenLink(base, tok)` with utm omitted; pass a `utm` object only
if you deliberately want GA4 tagging in the link). The destination is resolved
server-side from the token record. Entirely by design (spam/security hardening).

## Generate tagged links for outreach emails
Input CSV per batch (`leads.csv`), header row REQUIRED:
```
lead_id,campaign,dest1,ref1,dest2,ref2
lead_101,campaign-1,https://migma.io,pub-migma,https://techscoop.io/recap-2026,article-recap
```
Run (from the attribution dir; `NF_BASE_URL` optional, default
`https://newsletterfit.com`):
```
node utils/generate-links.js leads.csv        # -> links.csv (token-only links)
node utils/generate-links.js --lead lead_101 --campaign c --dest https://x.io --ref pub-x
```
Multiple links per lead = one per suggested pub/article, each with its own
token, so you can tell WHICH suggestion a lead clicked. Paste the returned
`link` URLs into the email (Outlook auto-links pasted URLs).

## Wire into Astro + Express backend
```js
import { AttributionStore, clickRouter, trackVisitMiddleware } from 'nf-attribution';
const store = new AttributionStore('./attribution.json');
app.get('/api/click', clickRouter(store, { cookieMaxDays: 30,
  // optional defense-in-depth: only allow redirects to listed domains
  // allowedDestinations: ['migma.io', 'newsletterfit.com']
}));
app.use(trackVisitMiddleware(store)); // run on page views
```
Flow: lead clicks -> `/api/click` records click + sets 30-day attribution cookie
-> 302 to real destination (UTM intact). Repeat visits on YOUR site while the
cookie lives are logged against that lead. External-pub clicks count as a click
but repeat-visit tracking only follows onto YOUR domain (the cookie is yours).

## Export to the live tracking sheet
Live sheet: ID `1wB0xxihPy4usJnVZHuN0W31rr26j05aZnL6ymzgbWLk`, keyed by **Company
Name**. Columns: `Company Name, Contact Emails, Title, Status, First Email,
Follow-up 1`.
- READ is free (link-shared CSV export via `curl .../export?format=csv`).
- `sync-to-sheet.js` merges per-lead summary (matched by Company Name) into new
  columns `Visited? | First Visit | Last Visit | Visit Count | Clicks`.
- **WRITE into the sheet needs Google OAuth** (google-workspace skill), which was
  NOT set up on the box as of 2026-08-28 (`setup.py --check` = NOT_AUTHENTICATED;
  no `~/.hermes/google_token.json`). Until the user completes the ~5-min free
  Google Cloud OAuth setup, `sync-to-sheet.js` produces a ready-to-import CSV
  (`lead_sheet_with_visits.csv`) rather than pushing live. Do NOT claim the sheet
  is updated until OAuth exists.

## Spam-safety decisions (why the links don't look spammy)
- Links live on the user's OWN domain, not a random shortener — the biggest
  deliverability factor.
- Token+UTM-only link, no `dest=`/external URL — looks like ordinary
  click-tracking (same pattern as HubSpot/Mailchimp), avoids open-redirect
  heuristics in Gmail/Outlook/AV.
- `allowedDestinations` allowlist refuses redirects to unlisted domains.
- Real deliverability is still driven by sending reputation (SPF/DKIM/DMARC,
  warm domain, engagement) — link structure does not fix a cold domain.

## Honest caveats to give the user
- Only emailed leads who click a token link get attributed. Anonymous visitors
  who never click can't be tied to a lead without a paid reverse-IP/company
  lookup — out of scope.
- Email OPENS are unreliable (Apple Mail auto-loads pixels); rely on clicks +
  visits instead.
- If a lead clears cookies, repeat visits after that stop linking (the first
  click still counts).