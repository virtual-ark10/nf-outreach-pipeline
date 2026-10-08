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
- Reading lead state from the CRM (leads engine on :3002; never a CSV).

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
The Leads CRM is the pad's Leads tab (newsletterfit.com/pad, engine on
127.0.0.1:3002) and the single source of truth for outreach state — the old
tracker is retired. Store: SQLite at `/home/boxed/resend-pad/data/outreach.db`
(node:sqlite; schema `/home/boxed/resend-pad/schema.sql`, tables leads,
lead_stage_events, emails, replies, drafts, events — the JSON stores were migrated
2026-09-10 and the old crm.json is now only a backup at
`/home/boxed/resend-pad/attic/pre-sqlite-*/`). A sponsor sits in the **"Leads"**
stage only until its first email is actually sent; sending from the pad (or a pad
draft sent through it) advances the stage, writes the emails row with the stage
frozen in `stage_at_send`, and logs the transition to `lead_stage_events`. "Sync
email" reconciles pad mail into the timeline and flags replies (a reply moves the
lead to Replied).

Stages: Leads (grey) → First Email (blue) → Follow-up 1-4 (teal/indigo/purple/
amber) → Qualified (cyan) → Replied (pink) → Won (green) / No (red) / Archived
(grey). `converted` (0/1 + converted_at, value_cents) is kept separate from stage,
so a deal can close without rewriting the pipeline. Companies whose first email is
still only a draft are LEADS, NOT "First Email". API (X-CRM-Token = pad token):
GET /api/leads, GET /api/leads/:id, GET /api/leads/:id/timeline,
PATCH /api/leads/:id {stage|priority|value_cents|converted|...},
POST /api/leads/:id/note, GET /api/pipeline (v_lead_pipeline),
GET /api/followups-due (v_followups_due), GET /api/emails, GET /api/replies,
PATCH/DELETE /api/replies/:id (the ✕ is a soft delete), POST /api/sync,
GET /api/meta (stages + counts + storage).

### 1c. AUTOMATED INTAKE (cron 'Sponsor intake to leads', daily 10:15 UTC)
Newly detected/confirmed sponsors are imported into the CRM automatically. The
job reads `/srv/newsletterfit/reports/sponsor-outreach/sponsor-leads.csv` (fresh
daily export, already carries per-sponsor draft emails), diffs against the leads
already in the CRM, and creates rows with stage="leads" (draft ready, NOT sent)
via `POST /api/leads` on the CRM (header `X-CRM-Token` = pad token). Max 5 per
run; Hunter domain-search only if >=5 searches remain (see the billing note in
section 2). Historical note: rows backfilled before the CRM existed are at
/home/boxed/backfill_rows.csv. Pull them in with the backfill tool, NOT the old
Python one (it wrote the retired crm.json and is in attic/):

```
node /home/boxed/resend-pad/tools/import-tracker.cjs --dry-run   # report first
node /home/boxed/resend-pad/tools/import-tracker.cjs             # writes the live db
```

It creates missing companies as LEADS, fills only missing fields on existing
ones, and files the tracker's First Email / Follow-up 1 columns into `drafts`
as UNSENT drafts — it never records a send or advances a stage, because the
column holds a draft body and the export has no send timestamp. Idempotent;
`--db <path>` aims it at a copy.

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
  this automatically (live pad queue -> resolve slugs -> mint internal tokens ->
  rewrite text+html -> PUT the drafts back to the pad + store + merge payload) is:
  `python3 /home/boxed/outreach_internalize.py` — the queue is SQLite behind the
  pad API, so `drafts.json` is retired; use `--dry-run` first, `--pad <url>` to
  point it elsewhere.
- **The pad fills the links in itself — you do not paste URLs into drafts.**
  Write each bullet as `<Pub> (est. <subs>): [TRACKED_LINK]`. The pad resolves the
  publication named on that line to its NewsletterFIT page, mints a token for it, and
  replaces the placeholder: on save over the API, before every send, and in a sweep
  every couple of minutes — so drafts written straight into the SQLite store (seeders,
  generators) are covered too. `[Name]` is replaced with the sender from the draft's
  From header. Check a whole queue with `POST /api/drafts/prepare-links` (pad token);
  it reports what it minted and anything it could not resolve. A draft that still
  contains `[TRACKED_LINK]` or `[Name]` is REFUSED at send, so an unfilled template
  can never reach a prospect. `python3 /home/boxed/outreach_internalize.py` (below) is
  still the tool for rewriting publication links that are already in a draft.
- **The subject says what you saw, never what stage it is.** A first touch's subject is
  `Spotted <Company> in <Publication>`, where the publication is the NEWEST placement in the
  corpus for that sponsor — `GET $NEWSLETTERFIT_API/directory/sponsors/<slug>` → newest
  `placements[].publishedAt` → `placement.publication.name`. `scripts/first_email_subject.py
  "<Company>"` prints the subject plus the placement's evidence string, and
  `--all-first-emails [--apply]` fixes a whole queue of drafts still labelled `First Email`.
  A subject that is just the cadence label (`First Email`) reads as a template and gets ignored.
- **Never let a raw tracking URL be the visible text of a link, and never repeat a name to
  host one** — the link goes on the FIRST mention: the publication at the head of its bullet
  (`- <a>Pub</a> — est. 169K`), the brand on the signature (`Founder, <a>NewsletterFIT</a>`,
  not `… — newsletterfit.com`). A visible `https://…/api/click?lt=<token>` reads as spam and
  does not get clicked. The pad's pass moves the link to the first mention, drops the
  repeated copy, and keeps token URLs out of the plain-text part.
- Pasted links carry ONLY the tracking token (`https://newsletterfit.com/api/click?lt=<TOKEN>`)
  — no UTM, no external URL, no `dest=` param. The destination is resolved
  server-side from the token record, so no external URL appears in the email
  link (keeps it from looking like an open redirect / spammy wrapped URL). The
  GA campaign params are added at the REDIRECT, not in the email: `/api/click`
  302s to the destination with `utm_source=newsletterfit&utm_medium=email` plus
  `utm_campaign=outbound` (the default for all outreach — set `OUTREACH_UTM_CAMPAIGN`
  on the API to change it) and `utm_content` from the token's `link_ref`, so emailed
  visits land in GA as an email campaign instead of direct/referral and no token needs
  re-minting. The token's own campaign slug stays in the attribution record.
- Requires the click route + visit middleware deployed in the Express backend
  (see `examples/express-integration.js`). Dest on YOUR site (`/pricing`,
  your own article) also tracks repeat visits via cookie.

```
{Greeting},

{N} {Company} placements in the newsletters I track. Most recently {Pub1}, plus
{Pub2} and {Pub3}.

I'm building NewsletterFIT: a corpus of newsletters that reads who sponsors whom and
which pubs are rising, so sponsors find fits for audiences they already pay for.

Same themes you already buy: {themes}.

- {Pub1} (est. {subs}): {TRACKED_LINK}
- {Pub2} (est. {subs}): {TRACKED_LINK}
- {Pub3} (est. {subs}): {TRACKED_LINK}

Want me to pull the reader profiles + momentum behind these three?

[Name], Founder, NewsletterFIT
```

## The send gate is the only way out

Nothing leaves without clearing `scripts/nf_send_gate.py` (cron job "NF daily outreach gate",
09:00 daily, script `nf-daily-outreach-gate.sh`). It reads the pad queue and the CRM and
holds a draft on any of:

- a figure the corpus contradicts: company placement counts, publication subscriber labels
  (an exact match is required, a loose name match is never treated as the publication, since
  a wrong "correction" is worse than an unresolved figure)
- a publication the corpus does not have at all
- a duplicate first touch (two drafts, same company) or a greeting that does not match the
  CRM's verified contact name, which is how a guessed first name shows up
- a lead who already received a first touch, or replied, or is unsubscribed/bounced
- apology, correction or back-reference language ("I made a mistake", "my earlier email
  said", "following up", "circling back", "to be transparent") — a cold sequence can never
  contain one, whichever direction the mistake went
- a stale date, an em-dash, a corpus-size claim, a broken or off-site tracked link
- cadence: a follow-up whose lead is not due yet waits in the queue

Placement counts are refreshed rather than held (`--fix-counts`), because the corpus ingests
continuously and the number is a live figure, not a claim that can be wrong. Everything else
is held and reported.

## Never send a correction or an apology

The register that damages a B2B reputation fastest is an email admitting the previous one was
wrong: "I made a mistake in the last email", "my original email flagged X but I went back
through the corpus and found zero...". If a draft is wrong, fix it before it goes, or drop it.
Once a wrong claim is out, do not send a correction email: correct the record with the
prospect only if they reply and ask, and never volunteer a mea culpa in a cold sequence.

## One contact per company

One live draft per company, addressed to the verified contact, not the first plausible
address found. Prefer the go-to-market or growth-marketing person (that is who buys
sponsorship intelligence). The CRM's `contact_name`/`contact_title` is the evidence: a draft
whose greeting does not match it, or whose address has no CRM record, is a guess and gets
held.

## Fact-check every figure at send time

Subscriber labels drift by a percent or two as the corpus grows, and a figure that was right
when the draft was written can be wrong by send day. Re-read each one from the corpus
(`/search?q=<name>`, exact name match, `subscribersLabel`) and re-read each company's
placement count before sending. The corpus is the source of truth, not the draft, and not the
recollection that a number was checked earlier.

**Placement counts have one house measure, and it is the export's.** `/srv/newsletterfit/
reports/sponsor-outreach/sponsor-leads.csv` carries the article-level count (sponsor-typed
mentions and confirmed sponsored articles, bucketed by article). The `/search` rollup
(`sponsors[].count`) is a subset of it and must never be quoted: Brex reads 34 in the export
and 14 in the rollup, Tracksuit 24 against 15, HubSpot 10 against 5. The product's own draft
generator quotes the export measure, and the send gate enforces it. Full evidence and method
in `docs/PLACEMENT-COUNT.md`. Where the export has no count for a company, state no count.

**No stale time references.** Anything anchored to "now" ages badly in a queue: "last
week (Aug 27, the FIVESTACK trade piece)", "twice this month, most recently Aug 28",
"5 placements since Aug 3", "in the last 30 days", "last issue yesterday", "recent issue
2 days ago". A draft can sit for days between writing and sending, so a dated claim is
wrong the moment the week turns over. Write either a date-free version ("5 placements in
Linear's Vertical Software & AI newsletter") or re-derive the figure from the corpus on the
day it sends. The pad cannot tell you a claim has aged: scan the body yourself before a
send. (Relative-but-current phrasing like "Most recently <Pub>" at the head of the opener
is fine, because it describes the newest placement rather than a date that passes.)

**No em-dashes.** Prose em-dashes are a recognised tell that a machine wrote the email;
the first emails shipped with five each (opener, theme line, every bullet) and it read
badly. Use a period, a comma, or a colon instead — "18 Brex placements in the newsletters I
track. Most recently Core Memory, plus Sourcery and The Generalist." Bullets carry the
subscriber count in parentheses, `- TheSequence (est. 169K)`, which `bulletName()` in
`pad/outreach-autolinks.cjs` parses (it strips a trailing parenthetical), so the pad can
still resolve the publication and mint the link.

**Craft in force (from the `cold-email` skill, MIT — merged with our grounding rules).**
The opener leads with the finding, never with us: the placement count and the newest
publication ARE the personalization, and they are the same facts the subject names. Then one
sentence of what NewsletterFIT is, then the match, then one low-friction ask. Keep it peer
voiced (contractions, read it aloud); "you/your" should outweigh "I/we"; no "I hope this
finds you well", no "leverage"/"synergy"/"best-in-class", no feature dumps, no meeting
request in a first touch, no "just checking in" follow-ups.

Two places we deliberately differ from that skill's defaults, because grounding is the
product: **tracked links stay** (it advises one link for deliverability; per-publication
attribution is the whole point), and **the measurement detail stays** ("7 placements",
"est. 169K", the theme list) even though it costs words — it is the proof this is not a
mail merge. Every number in the email must come from the corpus, never from memory.

**Never state a corpus size.** The number is small and moves every few minutes
(3,500+ newsletters and climbing), so `1,500+` is both stale and a needless
anchor — write "a corpus of newsletters". Same reasoning for any other
count-of-everything figure that a reader could hold you to.

**Never put momentum percentages in the email.** `Momentum: +12% / 90d` is what
the CTA is offering — printed in the body it spends the reason to reply and, as
three bare fragments per draft, it reads like leftover debug output. Momentum is
the thing they click through for, not the thing you hand over first.

### 3b. DRAFT DIFFERENTIATION (avoid template-identical emails)
Every draft in a batch must differ beyond the company name. Pull from the
sponsor's per-sponsor brief (`reports/sponsor-outreach/sponsors/<key>.md`):
- Repeat-buying signal: "5 placements since Aug 3, most recently #192 on
  going headless" — show you noticed the cadence, not just one placement.
- Topic-fit line: name the specific themes the sponsored pub covers (vertical
  SaaS + AI, AI search, AI agents, markets/culture) and why that maps to
  THEIR product, not the generic subscriber-count line.
- Momentum per recommended pub: use `recentActivity` + `Momentum` to CHOOSE and
  rank the recommendations — never to print a figure (see the rule above).
- Sponsor-booked proof: cite a KNOWN SPONSOR of a recommended pub (e.g.
  "already carries Stata and DeleteMe") — shows the list is ad-proven.
- Vary the subject line per lead; reference the last relevant article title
  when it's specific (e.g. Eli Schwartz's "Reddit should stop feeding Google").

Subject: `Spotted {Co} in {Pub}`, where {Pub} is a publication the BODY already names —
the newest placement the corpus holds for that company among those pubs, so subject and
body never point at different newsletters. `scripts/first_email_subject.py "<Company>"`
resolves it (and `--all-first-emails [--apply]` fixes a queue). Fill [Name] before sending.

### 3c. TWO LADDERS — `lead_source` picks the sequence

Every lead row carries `lead_source` (`corpus` or `icp_research`), derived from `source` by
`db.leadSourceFor` in the pad's db layer and backfilled by the CRM migration. Drafting branches
on it, because the two supplies support different claims.

`corpus` (source intake/import/pad_batch): we HAVE seen the company sponsor. Its copy quotes the
company's own export row (placement count, sponsored pubs, lookalike pubs). Drafted by
`scripts/seed_first_touch_drafts.py`; follow-ups by the 08:00 agent dig (4b).

`icp_research` (the lookalike scan): we have NOT seen the company sponsor. Of the first 84 such
leads exactly one appears in the sponsor export at all, so "saw you in X" would be fabricated and
there is no count to quote. Drafted by `scripts/seed_icp_drafts.py`, deterministically and on the
same 3/7/14 clock: the lists taking sponsors in the company's lane, the sponsors the corpus logs
running in them, and the reader profile behind each. No model drafts it, because there is no
prospect-specific dig to do.

The ICP copy must never state a placement count (the gate looks one up against the lead's own
export row, which does not exist, and returns a review), never claim the prospect appears
anywhere, and never use back-reference or apology wording. The lane map is data in `LANES` inside
that script and is expected to be tuned as lanes prove out.

The ladder is OFF until `data/icp-drafts.enabled` exists in the repo. The 08:30 job runs the
script either way; while the flag is absent it prints why and exits 0, so approving the copy is
creating one file. Preview it with `python3 scripts/seed_icp_drafts.py --sample 3` (and
`--touch 2 --sample 1` for a follow-up rung). `tests/test_icp_sequence.py` pins the selection and
runs the composed copy through the real send gate.

### 4. Follow-up sequence (proof-first, adopted from lead-gen)
Touches: day 3, day 7, day 14, then stop (4 touches = no).
- Touch 1: outreach email above.
- Touch 2 (day 3): one short line of NEW information, never a nudge. The gate HARD-holds
  back-reference wording ("bumping this", "following up", "circling back", "as I mentioned"),
  so a bump cannot be written as a bump: attach one more list, the audience behind one they
  have not seen, or the sponsors running in it.
- Touch 3 (day 7): add ONE new corpus evidence — a newly-grounded sponsorship,
  competitor buying similar pubs, or momentum for a recommended pub.
- Touch 4 (day 14): one final note — a concrete recent datapoint (competitor just
  bought a similar placement).
- These four are the CORPUS ladder. ICP leads run the same clock on a different, deterministic
  sequence (3c): the 08:00 intel job must not draft a follow-up for a `lead_source=icp_research`
  lead, or the two sequences mix and the buyer gets the wrong proof.
- Stop after 4. DROP below 2% after 200 sends → fix niche or evidence, not copy.

### 4b. Follow-up INTEL generation (the wow layer)
For each due follow-up, generate VERIFIED, prospect-specific data and an email frame using the
`email-follow-up-intel` skill (corpus + last30days + web, never invent). Deliver each prospect's
block to the Discord follow-up-intel channel (id: 1542548032254640168; daily 08:00 UTC job
'Follow-up intel daily scan'). See that skill for the per-prospect recipe, framing, and the
HARD no-fabrication rule.

### 5. Track in the Leads CRM
Leads live in the CRM: the **Leads tab inside the pad** (there is no separate
/crm/ page any more — the engine serves no UI of its own). Engine on
127.0.0.1:3002, proxied by the pad at `/api/crm/*`, reading and writing SQLite at
`/home/boxed/resend-pad/data/outreach.db` (one `X-Pad-Token` unlocks both the pad
and the CRM). Stage changes, notes, sent mail and replies are all rows — nothing
is stored in JSON files any more. Log Hunter credits per batch in a lead note.
Backlog of stages: Leads, First Email, Follow-up 1-4, Qualified, Replied, Won, No,
Archived. Reports come from the two views: `v_lead_pipeline` (per lead: stage,
converted, emails_sent, replies, last_reply_at) and `v_lead_timeline` (mail and
stage moves in one chronological stream), plus `v_followups_due` for the touch
cadence. Commit a state snapshot with
`/home/boxed/nf-outreach-pipeline/scripts/snapshot-state.sh` after a batch.

## Reusable artifacts
- Lead state lives in the CRM: `pad/data/outreach.db` behind the leads engine on
  :3002. `scripts/crm_lead_state.py` prints in-flight, due and expired leads. The old
  `/home/boxed/lead_tracking_sheet.csv` is RETIRED and must not be read.
- `/home/boxed/sponsor_evidence.json`
- `/home/boxed/sponsor_legit_class.json`
- `/home/boxed/hunter_emails.json`, `/home/boxed/emails_out/*.md`,
  `/home/boxed/hunter-contacts.csv`, `/home/boxed/hunter_best_contacts.json`,
  `/home/boxed/combine_contacts.json`.
- Click/visit attribution: `/home/boxed/newsletterfit/attribution`
  (`utils/generate-links.js` -> token-tagged links; `utils/export-visits.js` ->
  per-lead visit/click data for the CRM; zero-dep JSON store, vanilla JS).

## Pitfalls
- Hunter `company=` unreliable for ambiguous names → pass confirmed `domain=`.
- 401 on malformed key → api_key exact.
- Free Hunter caps limit=10; limit=25 → pagination_error.
- Hunter verification drains separate pool — check /v2/account.
- /srv/newsletterfit not writable → write under /home/boxed.
- Series recap shows bundling (Brex+MongoDB+AssemblyAI) — treat each name as its
  own sponsor.