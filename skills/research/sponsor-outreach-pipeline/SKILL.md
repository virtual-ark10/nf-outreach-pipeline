---
name: sponsor-outreach-pipeline
description: "Run NewsletterFIT sponsor outreach end-to-end."
version: 1.0.0
author: Nous Research
license: MIT
platforms: [linux]
---

# Sponsor Outreach Pipeline

Turns the NewsletterFIT read-only corpus + Hunter.io into a repeatable outbound
lead-gen flow for sponsors (brands) that buy newsletter ads. The product
NewsletterFIT sells is: "we find newsletters similar to the ones you already
sponsor, using observed behavior not subscriber-count vanity."

## When to use
- Generating sponsor outreach email batches.
- Enriching a sponsor list with contacts.
- Verifying whether a corpus "sponsor" is a real ad buyer or a false positive.
- Building follow-up sequences.
- Syncing/reading the lead-tracking Google Sheet.

## Golden rule — VERIFY SPONSORS BEFORE EMAILING
The `all-sponsors.json` export (reports/sponsor-outreach/) OVER-CLAIMS. It lists
placements that do NOT exist in the corpus. ALWAYS confirm a sponsor is real
before sending. Uber was flagged HIGH/82 & "recently sponsored Tech Scoop" but
had ZERO grounded sponsor articles — the Tech Scoop claim was fabricated and the
one True placement was casual prose ("I think it was actually sponsored by Uber
Eats") caught by the `sponsored_by` extraction. Proof-first means the metadata
proves the ad, not the other way around.

### The reliable signal
Per-article `analysis.sponsors[]` with `status: "confirmed"` and a real ad
evidence string. In this corpus (2026-08-26) ~239 distinct confirmed sponsor
names, each backed by an article + evidence. Query:

```js
db.articles.find(
  {"analysis.sponsors.name":/^Tracksuit$/i, "analysis.sponsors.status":"confirmed"},
  {title:1, publicationName:1, "analysis.sponsors":1}
)
```

### Legitimacy classifier
For each sponsor, pull confirmed articles' `analysis.sponsors[].evidence`.
LEGIT only if evidence has ad markers: `coupon | discount | % off | credits |
partnership | disclosure | "sponsored by" | "brought to you by" | "our sponsor" |
"thank you to" | "(sponsored" | "$25"`.
FALSE-POSITIVE if casual/procedural: `"i think" | maybe | probably | "a federal
bill" | "sponsored by rep." | legislation | senator | congress`.
Verdicts: LEGIT-MULTI (>=2) / LEGIT-SINGLE / UNCLEAR / FALSE-POSITIVE /
NO-GROUNDED. Email only LEGIT*. Classifier output:
`/home/boxed/sponsor_evidence.json` + `/home/boxed/sponsor_legit_class.json`.

## Sources of truth
- **Corpus (read-only)**: `/home/boxed/.config/newsletterfit/corpus.env`
  (`$MONGODB_URI`, `$NEWSLETTERFIT_API`). Prefer API `GET $NEWSLETTERFIT_API/search`.
- **Export**: `/srv/newsletterfit/reports/sponsor-outreach/all-sponsors.json`
  (score, angle, categories, topics, recommendations, email draft). newsletterfit-
  owned: read OK; write → /home/boxed.
- **Hunter.io**: free 50 searches/mo + 100 verifications/mo. Key: read it from
  `$HUNTER_API_KEY` (stored in `~/.hermes/.env`) — NEVER hardcode it into a file
  that can end up committed. Report spend; rotate when exhausted. Key rotated
  2026-09-10 after the previous one hit 0/50 + 0/100 (new pool resets 2026-10-10).
  Account: `curl -s "https://api.hunter.io/v2/account?api_key=$HUNTER_API_KEY"`.

## Pipeline stages (repeat per sponsor batch)

### 1. Select + verify sponsors
Start from high-priority.md / all-sponsors.json. Verify each against corpus
(classifier above). DROP false positives + NO-GROUNDED. For short/ambiguous names
(Imagine, Rep, AWS) resolve the correct domain first — Hunter guessed
Imagine→imagine.io, AWS→aws.ac.th, Unblocked→unblocked.life.

### 1b. Stage leads: LEADS bucket = FIRST EMAIL not yet sent
The Leads CRM (newsletterfit.com/crm/, store /home/boxed/nf-crm/data/crm.json) is
the single source of truth for outreach state — the old tracker is retired. A
sponsor sits in the **"Leads"** stage only until its first email is actually
sent; sending from the CRM (or a pad draft sent through it) advances the stage and
logs the email against the lead automatically. "Sync email" in the CRM reconciles
pad mail into the timeline and flags replies (a reply moves the lead to Replied).

Stages: Leads (grey) → First Email (blue) → Follow-up 1-4 (teal/indigo/purple/
amber) → Replied (pink) → Won (green) / No (red). Companies whose first email is
still only a draft are LEADS, NOT "First Email". API (X-CRM-Token = pad token):
GET /api/leads, PATCH /api/leads/:id {stage}, POST /api/leads/:id/note,
POST /api/sync, GET /api/meta (stages + counts).

### 1c. AUTOMATED INTAKE (cron 'Sponsor intake to leads', daily 10:15 UTC)
Newly detected/confirmed sponsors are imported into the CRM automatically. The
job reads `/srv/newsletterfit/reports/sponsor-outreach/sponsor-leads.csv` (fresh
daily export, already carries per-sponsor draft emails), diffs against the leads
already in the CRM, and creates rows with stage="leads" (draft ready, NOT sent)
via `POST /api/leads` on the CRM (header `X-CRM-Token` = pad token). Max 5 per
run; Hunter domain-search only if >=5 searches remain (see the billing note in
section 2). Historical note: rows backfilled before the CRM existed are at
/home/boxed/backfill_rows.csv and can be pulled in with
`python3 /home/boxed/nf-crm/import_tracker.py --csv <file>`.

### 2. Find contacts (Hunter)
Domain-search: `GET /v2/domain-search?domain=<d>|company=<n>&type=personal
&decision_maker=true&limit=10` (free caps at 10). Returns people+email in ONE
credit, flags decision-makers. Discover=free but returns companies not people.
Prefer `domain=` over `company=`.
Target `position` — FIRST CHOICE: **GTM / go-to-market, then growth** (founder
rule, 2026-09-10: "always go with the GTM person or growth person as first
choice if available"). Only fall back down the list when no GTM/growth person
exists at the domain: partnerships > marketing > CRO/BD > founder.
Skip HR/People/engineering. Match on title keywords: gtm, go-to-market, growth,
demand gen, revenue, expansion — and treat "Head of GTM Strategy" / "Growth
Lead" style titles as the win, not merely acceptable.

**Hunter billing measured 2026-09-10:** each charged `domain-search` call
consumes **1 search AND 2 verifications**. A domain it has no people for (e.g.
tryprofound.com) returns `organization` but no emails and costs nothing. The
`department=` filter (e.g. `department=marketing`, `department=sales`) is the
way to reach GTM/growth people at big companies whose top-10 is all
engineering — plain `domain-search` on brex.com returned only eng/design, while
`department=marketing` surfaced the growth-marketing directors. Budget BOTH
pools: 50 searches and 100 verifications move in lockstep (1:2), so 100
verifications ≈ 50 searches' worth of lookups.

### 3. Write proof-first personalized email (draft from corpus, in LEADS stage)
Draft while the lead is still in the Leads/First Email bucket — drafting does NOT
send it. Each email cites a REAL grounded placement (stage 1, `analysis.sponsors[]
status:"confirmed"` + ad evidence) + 2-3 corpus lookalike publications with est.
subscribers + 90d momentum + a topic-fit line. Plain text, no markdown. Pull the
lookalikes from the sponsor's `recommendations[]` in all-sponsors.json (those carry
real subscriber counts, similarity scores, and momentum) — never invent a subscriber
number or a placement. Fill [First Name] and [Name] before sending. After the draft
is approved and sent, update the sheet: status → "First Email", paste the body into
the First Email column, add the send date.

**Tag every recommended pub/article as a tracked link.** Instead of listing a
bare pub name, turn each suggestion into a click-tracking link from the
nf-attribution package (`/home/boxed/newsletterfit/attribution`). This answers
"which lead clicked which suggestion" and feeds the lead-tracking sheet. Flow:
- Create `links.csv` per batch (or per lead) with `utils/generate-links.js`:
  columns `lead_id,campaign,dest1,ref1,dest2,ref2,...` where `destN` is the real
  URL of the suggested pub/article and `refN` its label (e.g. `pub-migma`,
  `article-recap-2026`). Run:
  `node utils/generate-links.js leads.csv > links.csv`
- **ALL OUTREACH LINKS MUST BE INTERNAL** (policy, 2026-09-08): never send a
  lead to an external site (substack.com, thedigitalcreator.co, etc.). For each
  suggested pub, resolve its NewsletterFIT page first — the search API
  (`GET $NEWSLETTERFIT_API/search?q=<pub name>`) returns the canonical `slug`,
  then dest = `https://newsletterfit.com/app/publications/<slug>`. Only mint a
  token whose `dest` starts with `https://newsletterfit.com`. The loop that does
  this automatically (drafts.json -> resolve slugs -> mint internal tokens ->
  rewrite text+html -> write drafts.json + store + merge payload) is:
  `python3 /home/boxed/outreach_internalize.py`
- Paste the returned `link` URLs into the email lines below (Outlook auto-links
  pasted URLs).
- Pasted links carry ONLY the tracking token (`https://newsletterfit.com/api/click?lt=<TOKEN>`)
  — no UTM, no external URL, no `dest=` param. The destination is resolved
  server-side from the token record, so no external URL appears in the email
  link (keeps it from looking like an open redirect / spammy wrapped URL).
- Requires the click route + visit middleware deployed in the Express backend
  (see `examples/express-integration.js`). Dest on YOUR site (`/pricing`,
  your own article) also tracks repeat visits via cookie.

```
Hi {First},

Quick one — I track newsletter sponsorships closely and saw {Company} is running
in {real pubs, comma-sep}.

I'm building NewsletterFIT — a corpus of 1,500+ newsletters, reading who sponsors
whom, what each audience reacts to, and which pubs are rising. Goal: help sponsors
find fits for the audiences they already pay for, on observed behavior.

From that signal these matched the work you're already doing:
- {Pub} — est. {subs}: {TRACKED_LINK}
- {Pub2} — est. {subs}: {TRACKED_LINK}
- {Pub3} — est. {subs}: {TRACKED_LINK}

Want me to pull reader profiles + momentum + their booked sponsorships?
[Name], Founder, NewsletterFIT — newsletterfit.com
```

### 3b. DRAFT DIFFERENTIATION (avoid template-identical emails)
Every draft in a batch must differ beyond the company name. Pull from the
sponsor's per-sponsor brief (`reports/sponsor-outreach/sponsors/<key>.md`):
- Repeat-buying signal: "5 placements since Aug 3, most recently #192 on
  going headless" — show you noticed the cadence, not just one placement.
- Topic-fit line: name the specific themes the sponsored pub covers (vertical
  SaaS + AI, AI search, AI agents, markets/culture) and why that maps to
  THEIR product, not the generic 800K+ line.
- Momentum per recommended pub: use `recentActivity` + `Momentum` (e.g.
  "+57% subscriber growth in 90 days").
- Sponsor-booked proof: cite a KNOWN SPONSOR of a recommended pub (e.g.
  "already carries Stata and DeleteMe") — shows the list is ad-proven.
- Vary the subject line per lead; reference the last relevant article title
  when it's specific (e.g. Eli Schwartz's "Reddit should stop feeding Google").

Subject: `Saw {Co} in {Pub1} + {Pub2}` or a differentiated variant. Fill [Name] before sending.

### 4. Follow-up sequence (proof-first, adopted from lead-gen)
Touches: day 3, day 7, day 14, then stop (4 touches = no).
- Touch 1: outreach email above.
- Touch 2 (day 3): one short bump, no re-pitch. "Bumping this — happy to send the
  lookalike numbers."
- Touch 3 (day 7): add ONE new corpus evidence — a newly-grounded sponsorship,
  competitor buying similar pubs, or momentum for a recommended pub.
- Touch 4 (day 14): one final note — a concrete recent datapoint (competitor just
  bought a similar placement).
- Stop after 4. DROP below 2% after 200 sends → fix niche or evidence, not copy.

### 4b. Follow-up INTEL generation (the wow layer)
For each due follow-up, generate VERIFIED, prospect-specific data and an email frame using the
`email-follow-up-intel` skill (corpus + last30days + web, never invent). Deliver each prospect's
block to the Discord follow-up-intel channel (id: 1542548032254640168; daily 08:00 UTC job
'Follow-up intel daily scan'). See that skill for the per-prospect recipe, framing, and the
HARD no-fabrication rule.

### 5. Track in the Leads CRM
Leads live in the CRM: `https://newsletterfit.com/crm/` (store
`/home/boxed/nf-crm/data/crm.json`, API on 127.0.0.1:3002, Caddy route `/crm/*`).
Stage changes, notes, sent mail and replies are logged per lead; the CRM's "Sync
email" reconciles the pad's mail against leads. Log Hunter credits per batch in a
lead note. Backlog of stages: Leads, First Email, Follow-up 1-4, Replied, Won, No.
The CRM is read/written with the pad token (`X-CRM-Token`), so no Google OAuth is
needed any more.

## Reusable artifacts (built 2026-08-26)
- `/home/boxed/lead_tracking_sheet.csv`
- `/home/boxed/sponsor_evidence.json`
- `/home/boxed/sponsor_legit_class.json`
- `/home/boxed/hunter_emails.json`, `/home/boxed/emails_out/*.md`,
  `/home/boxed/hunter-contacts.csv`, `/home/boxed/hunter_best_contacts.json`,
  `/home/boxed/combine_contacts.json`.
- Click/visit attribution: `/home/boxed/newsletterfit/attribution`
  (`utils/generate-links.js` -> token-tagged links; `utils/export-visits.js` ->
  per-lead visit/click CSVs for the lead sheet; zero-dep JSON store, vanilla JS).

## Pitfalls
- Hunter `company=` unreliable for ambiguous names → pass confirmed `domain=`.
- 401 on malformed key → api_key exact.
- Free Hunter caps limit=10; limit=25 → pagination_error.
- Hunter verification drains separate pool — check /v2/account.
- /srv/newsletterfit not writable → write under /home/boxed.
- Series recap shows bundling (Brex+MongoDB+AssemblyAI) — treat each name as its
  own sponsor.