# The pipeline end to end

Operating manual. Every command here is the one that actually runs on the
NewsletterFIT box. Paths are absolute because that is how the box is laid out;
nothing in `pad/` itself hardcodes them.

## Sources of truth

| Thing | Where |
| --- | --- |
| Newsletter corpus (read-only) | `/home/boxed/.config/newsletterfit/corpus.env` (`$MONGODB_URI`, `$NEWSLETTERFIT_API`, `$API_BEARER_TOKEN`) |
| Sponsor export | `/srv/newsletterfit/reports/sponsor-outreach/all-sponsors.json` (read OK, never write) |
| Daily intake CSV | `/srv/newsletterfit/reports/sponsor-outreach/sponsor-leads.csv` |
| Lead state (the CRM) | **SQLite** at `/home/boxed/resend-pad/data/outreach.db` (`pad/data/outreach.db` in this repo); API `127.0.0.1:3002` |
| Mail | Resend (the pad's inbox/sent are live API views, 30-day retention) — everything the pipeline did is a row in `emails` / `replies` |
| Read the state | `node pad/tools/db-report.cjs` (or `--lead <id>` for one timeline) |
| Contacts | Hunter.io, free tier: 50 searches + 100 verifications per month |

## 0. Start the services

```bash
cd pad && ./boot.sh          # pad on 127.0.0.1:3001
cd pad/leads && ./boot.sh    # leads engine on 127.0.0.1:3002
```

Health: `GET /api/meta` on the CRM returns the stage list, per-stage counts and
the storage it is reading. Both processes open the same SQLite file
(`pad/data/outreach.db`, WAL mode); a minute-by-minute cron watchdog restarts
whichever one dies.

## 1. Select and verify sponsors

Never trust `all-sponsors.json` on its own — it over-claims. A sponsor is real
only when an article carries `analysis.sponsors[]` with `status: "confirmed"`
plus an evidence string that reads like an ad.

```js
db.articles.find(
  {"analysis.sponsors.name": /^Tracksuit$/i, "analysis.sponsors.status": "confirmed"},
  {title: 1, publicationName: 1, "analysis.sponsors": 1}
)
```

Classify each candidate as LEGIT-MULTI (>=2 confirmed) / LEGIT-SINGLE / UNCLEAR /
FALSE-POSITIVE / NO-GROUNDED using ad markers in the evidence (`coupon`,
`discount`, `% off`, `credits`, `partnership`, `disclosure`, `sponsored by`,
`brought to you by`, `our sponsor`, `thank you to`, `(sponsored`, `$25`). Email
only LEGIT*. Casual prose ("I think it was actually sponsored by Uber Eats") is
not proof.

Outputs: `artifacts/sponsor_evidence.json`, `artifacts/sponsor_legit_class.json`.

Resolve ambiguous names to a domain *before* Hunter sees them — Hunter has
guessed Imagine -> imagine.io, AWS -> aws.ac.th, Unblocked -> unblocked.life.

## 2. Stage the leads

Create each lead in stage `Leads` (the draft exists, nothing is sent):

```bash
curl -s -X POST 127.0.0.1:3002/api/leads \
  -H "X-CRM-Token: $PAD_TOKEN" -H 'Content-Type: application/json' \
  -d '{"company":"Tracksuit","domain":"tracksuit.com","contact_name":"...","contact_email":"...","contact_role":"gtm","source":"intake"}'
```

Automated intake: the cron job *Sponsor intake to leads* (daily 10:15 UTC) reads
`sponsor-leads.csv`, diffs against existing leads, and creates up to 5 rows per
run with `stage="leads"`. Rows backfilled from before the CRM existed are in
`artifacts/backfill_rows.csv`; load them with

```bash
python3 pad/leads/import_tracker.py --csv artifacts/backfill_rows.csv
```

## 3. Find the contact (Hunter)

```bash
curl -s "https://api.hunter.io/v2/domain-search?domain=tracksuit.com&type=personal&decision_maker=true&limit=10&api_key=$HUNTER_API_KEY"
```

Target order — **GTM / go-to-market first, then growth** (founder rule,
2026-09-10). Only fall back when no GTM/growth person exists at the domain:
partnerships > marketing > CRO/BD > founder. Skip HR/People/engineering.

Billing reality: each charged `domain-search` costs **1 search and 2
verifications**, so the two pools drain in lockstep (100 verifications ≈ 50
searches). A domain Hunter has no people for costs nothing. For large companies
whose top-10 is all engineering, use `department=marketing` to reach the growth
people. Check spend with `GET /v2/account` and log it as a lead note.

## 4. Write the email (still in Leads)

Proof-first, plain text, no markdown. Every claim must be grounded in the corpus:

- one REAL placement the sponsor already runs (stage 1 evidence),
- 2-3 lookalike publications with estimated subscribers and 90-day momentum,
  pulled from the sponsor's `recommendations[]` — never invented,
- a topic-fit line naming what those publications actually cover,
- a repeat-buying signal when there is one ("5 placements since Aug 3").

Differentiate every draft in a batch beyond the company name (subject line,
momentum figures, topic fit). Fill `[First Name]` and `[Name]` before sending.

## 5. Mint internal tracking links

```bash
# per batch: lead_id,campaign,dest1,ref1,dest2,ref2,...
node attribution/utils/generate-links.js leads.csv > links.csv

# or, for the live drafts (resolve slugs -> mint internal tokens -> rewrite
# text+html -> write drafts.json + store + merge payload):
python3 scripts/outreach_internalize.py
```

Policy: `dest` must be `https://newsletterfit.com/app/publications/<slug>`. Get
the slug from `GET $NEWSLETTERFIT_API/search?q=<pub name>`. The pasted link is
`https://newsletterfit.com/api/click?lt=<TOKEN>` — the destination is resolved
server-side from the token record. The click route and visit middleware must be
deployed in the site backend (`attribution/examples/express-integration.js`).

Requires `NEWSLETTERFIT_API` + `API_BEARER_TOKEN`; the pad fails closed and
blocks a send whose links are external or stale.

## 6. Send

Send from the pad (or send a pad draft) — the pad is the single sending path, so
the CRM sees the mail. On success the lead advances one stage and the activity
log gets `email_out` plus `auto-advanced by send`.

Run `POST /api/sync` afterward (or on a schedule) to reconcile: it logs pad mail
into the timeline, heals stale `Leads` rows, and moves repliers to `Replied`.

Backfill: mail sent before the CRM existed is picked up by the same sync.

## 7. Follow up

Cadence (data, `LEAD_DUE_DAYS`): day 3, day 7, day 14, then stop. Four touches and
no reply means No — drop leads below a 2% reply rate, because that is a targeting
or evidence problem, not a copy problem.

- Touch 2: one short bump, no re-pitch.
- Touch 3: add ONE new grounded evidence item (a fresh placement, a competitor
  buying similar pubs, or fresh momentum).
- Touch 4: one concrete final datapoint, then stop.

`email-follow-up-intel` (skill) builds the verified per-prospect intel pack for
each due touch from corpus + last30days + web. Hard rule: never invent a fact.

## Pitfalls

- Hunter `company=` is unreliable for ambiguous names — pass a confirmed
  `domain=`.
- Hunter free tier caps `limit=10`; `limit=25` returns a pagination error.
- Verification is a separate pool from search — check `/v2/account`.
- `/srv/newsletterfit` is not writable: write outputs under `/home/boxed`.
- Series recaps bundle sponsors (Brex + MongoDB + AssemblyAI in one post) —
  treat each name as its own sponsor.
- Resend keeps email 30 days; the pipeline's own history lives in
  `pad/data/outreach.db` (leads, stage history, every message, replies) and is
  mirrored into git by `scripts/snapshot-state.sh`. Run it after a batch — and
  never leave `OUTREACH_DB` set in a shell that starts the live services.
