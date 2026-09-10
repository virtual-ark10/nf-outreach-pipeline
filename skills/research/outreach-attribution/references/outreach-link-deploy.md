# nf-attribution: "minted but 404" deploy checklist (learned 2026-09-08)

When freshly minted tokens resolve as 404/`Unknown or expired link` on
https://newsletterfit.com/api/click, work this list top to bottom. In every case
seen so far the fix was environmental, NOT a code change — the router and
middleware are shipped in the repo and mounted in app.js.

## 1. Is the route mounted at all?
`/srv/newsletterfit/app.js` mounts the trio ONLY under an env gate:

```js
if (env.enableOutreachAttribution) {   // ENABLE_OUTREACH_ATTRIBUTION in /etc/newsletterfit/newsletterfit.env
  app.get("/api/click", attribution.clickHandler);
  app.get("/api/attribution/pv", attribution.beaconHandler);
  app.use(attribution.visitMiddleware);
}
```

Distinguish "handler not mounted" from "store miss" before touching anything:
- `curl -s "https://newsletterfit.com/api/click?lt=bogus"` → body
  `Unknown or expired link` = handler IS mounted, lookup failed.
- A framework 404 (HTML "Cannot GET") = not mounted / flag off.

## 2. Which store file is the process reading?
- DEV/writable minting store: `/home/boxed/newsletterfit/attribution/attribution.json`
  (boxed-owned; `generate-links.js` and `outreach_internalize.py` write here).
- LIVE store: `/var/lib/newsletterfit/attribution.json`
  (owner newsletterfit, mode 640 — boxed can neither read nor write it; no sudo).
  Any merge into the live store is a root/deploy-gate action.

## 3. THE GOTCHA: AttributionStore caches the file at boot
`attribution/src/store.js` `_load()` reads the JSON ONCE in the constructor and
keeps it in memory; every event writes back but nothing ever re-reads. So a
token appended to the file AFTER the API process started is invisible until the
process restarts.

Real timeline from the session:
- API restarted 12:49 UTC with probe token already in file → probe 302'd.
- Sep2 batch tokens merged into the file at 13:02 — SAME file, SAME process →
  all 15 returned 404 `Unknown or expired link` even though the tokens were
  verifiably present.
- Fix was not a code change: merge, then `systemctl restart newsletterfit-api.service`.

Order irreducibly matters: **write the file first, then restart.** If you restart
first, tokens merged afterward repeat the exact same symptom.

## 4. Verification probe (after every merge + restart)
```sh
# expect: HTTP/2 302, location: <dest>, set-cookie: nf_attr=...; HttpOnly; Secure; SameSite=Lax
curl -sI "https://newsletterfit.com/api/click?lt=<TOKEN>"
# beacon (expect 204):
curl -s -o /dev/null -w "%{http_code}" "https://newsletterfit.com/api/attribution/pv?path=/pricing"
```
Reusable: `scripts/verify-tracking-links.sh <token> [token2 ...]` in this skill.

## Merge payload format
`outreach_internalize.py` writes `/home/boxed/newsletterfit/attribution/attribution.new-tokens.json`:
`{"tokens": [ {token, lead_id, campaign, link_ref, dest, created_at, expires_at, first_click_at}, ... ]}`
Merge = append `tokens[]` into live store `clicks[]`, preserve existing rows,
then restart the service.

## Internalize loop (ALL OUTREACH LINKS INTERNAL — policy 2026-09-08)
`python3 /home/boxed/outreach_internalize.py [--dry-run] [--drafts PATH] [--store PATH] [--out-tokens PATH]`
- Walks each Pad draft (text + html), finds `lt=<token>` links, looks up the
  stored `dest`, resolves the human pub name → canonical slug via
  `GET $NEWSLETTERFIT_API/search?q=<name>`, mints or reuses internal tokens
  (dedupe key `(lead_id, internal_dest)` — reuses, never dupes), rewrites
  drafts, and reports unresolved rows so nothing external sneaks through.
- Pitfall: pub names sit in bullets "- Name — detail — link"; parse by
  splitting the line on em/endash, not by regex-hopping on the whole body
  (that returns empty strings).
- `NEWSLETTERFIT_API` in corpus.env already includes `/api/v1` — do not
  double-prefix.

## Integration into the pipeline
Full outreach workflow lives in the `sponsor-outreach-pipeline` skill (stage 3,
tracked-links section); this file is the deployment addendum for the
outreach-attribution stack itself.