# nf-attribution

Lead-level visit attribution for NewsletterFIT. Answers the question: **"which leads did we email actually visit our site, and when?"**

It adds the layer GA4 and HubSpot/Mixmax intentionally don't: connecting a link click back to a **specific lead** and tracking their **repeat visits** over the following days.

## The three-part stack (all free)

| Layer | Job | Tool |
|---|---|---|
| Traffic & source | total visits, UTM, referrers, countries | GA4 (you have it) |
| Click tracking | who opened/clicked your email links | HubSpot Free / Mixmax |
| **Lead attribution** | **which emailed lead visited, and how often** | **this package** |

## How it works

1. Before emailing a lead, you issue a token and build a link:
   ```js
   import { generateToken, buildTokenLink, AttributionStore } from 'nf-attribution';
   const store = new AttributionStore('./attribution.json');
   // linkRef = which suggestion this link guards; dest = where the click lands
   // (stored SERVER-SIDE, never placed in the email link)
   const tok = generateToken('lead_123', 'campaign-1', 'pub-migma', 'https://migma.io');
     store.addToken(tok);
     const link = buildTokenLink('https://newsletterfit.com', tok);
     console.log(link);
     ```
     The email link looks like ordinary click-tracking — token ONLY, no
     external URL, no `dest=`, no UTM (keeps the link short):
     `https://newsletterfit.com/api/click?lt=<TOKEN>`

   Put **as many as you like** in one email — one per suggested pub, one per
   suggested article. Each carries its own token, so a click tells you both
   WHICH lead and WHICH suggestion they engaged with.

2. The lead clicks the link in your email. The click hits `/api/click`, records
   the click (against that lead + that link_ref), drops an attribution cookie,
   and **302-redirects** to the real destination (resolved server-side from the
   token record) — any UTM params present on the link are passed through, so if
     you ever choose to tag with GA4 it still credits the campaign. If the
     destination is a page on YOUR site (a `/pricing`
   style path or your own article), repeat-visit tracking kicks in over the
   following days. An optional `allowedDestinations` allowlist refuses redirects
   to any domain you haven't listed — defense-in-depth against open-redirect
   abuse.

3. Over the next days, every page on your site they load while the cookie is
   valid is logged as a visit **against that lead**.

4. Export the results and feed your lead-tracking sheet:
   ```bash
   node utils/export-visits.js            # summary table
   node utils/export-visits.js --csv out  # visits.csv + summary.csv
   ```
   `visits.csv` includes a `link_ref` column so you can see which suggestion
   (pub vs article) each lead actually engaged with.

## Wire into Express / Astro+Express

See `examples/express-integration.js`. The essentials:

```js
import { AttributionStore, clickRouter, trackVisitMiddleware } from 'nf-attribution';
const store = new AttributionStore('./attribution.json');

// 1) the click endpoint your email links point at
app.get('/api/click', clickRouter(store, { cookieMaxDays: 30 }));

// 2) site-wide visit tracking (run on page views)
app.use(trackVisitMiddleware(store));
```

## Running against your real backend

- The included store is a **zero-dependency JSON file** (`attribution.json`) — no compile step, works on any Node. Fine for low-volume attribution.
- Prefer Postgres/MySQL? Implement the same five methods (`addToken`, `lookupToken`, `recordClick`, `recordVisit`, `visitsForLead`, `summary`) with your driver and swap it in. The rest of the package won't change.

## Filling the lead-tracking sheet

`--csv` outputs two clean CSVs. To fold them into your live Google Sheet: either import the `summary.csv` columns into the lead sheet (new columns like "Visited?", "First Visit", "Last Visit", "Visit Count"), or wire `utils/export-visits.js` to push those columns via the Sheets API — tell me the sheet ID and I'll write that specific updater.

## Tests

```bash
node test/smoke.js   # 15 checks, end-to-end
```

## Caveats

- Email **opens** are unreliable (Apple Mail auto-loads pixels); this package measures **clicks + site visits**, which are real signals.
- It attributes visits to **emailed leads only** (those who click a token link). Anonymous visitors who never click an email can't be tied to a lead without a paid reverse-IP company lookup — out of scope here.
- Cookie-based: if the lead clears cookies, repeat visits after that won't link (the first click still is).