# nf-outreach-pipeline

The NewsletterFIT sponsor outreach pipeline, end to end, in one repo: the CRM that
tracks every lead and its stage, the pad that sends and receives the mail, the
attribution package that makes every link in every email measurable, the scripts
that wire those together, and the agent skills that describe how to run all of it.

The product this pipeline sells: *we find newsletters similar to the ones you
already sponsor, using observed behavior rather than subscriber-count vanity.*

Assembled 2026-09-10 from the live deployment on the NewsletterFIT box.
Provenance: `pad/` from `/home/boxed/resend-pad`, `attribution/` from
`/home/boxed/newsletterfit/attribution`, `skills/` from
`~/.hermes/skills/research/*`, `artifacts/` from the pipeline's own output.

**No secrets are in this repo.** `pad/.env.example` documents every variable that
the pad and the CRM read; the real `.env` stays on the box.

---

## The pipeline in one pass

```
        corpus (read-only)                    Hunter.io (free tier)
  /srv/newsletterfit/reports/...          domain-search -> people + email
              |                                        |
              v                                        v
   [1] select + verify sponsors            [2] find the GTM/growth contact
       (confirm real placements,               (GTM > growth > partnerships
        drop false positives)                   > marketing > CRO > founder)
              |                                        |
              +-------------------+--------------------+
                                  v
                    [3] draft proof-first email
                        cites a REAL grounded placement
                        + 2-3 corpus lookalikes with momentum
                                  |
                                  v
                    [3b] mint internal tracking links
                        (nf-attribution, dest pinned to newsletterfit.com)
                                  |
                                  v
                    [4] send from the pad  -------------> Resend
                        pad: 127.0.0.1:3001                (30-day retention)
                                  |
                        SQLite records the message with the stage
                        frozen at send time, and moves the stage
                                  |
                    [5] replies + clicks land back in the CRM
                        (webhook / sync -> replies row -> Replied; tokens -> visits)
                                  |
                    [6] follow-up intel per due touch (day 3 / 7 / 14)
                        corpus + last30days + web, never invented
```

## What's in here

| Path | What it is |
| --- | --- |
| `pad/` | The sending/receiving pad (zero-dependency Node). Server-side Resend key, token-gated API, inbox/sent/drafts, and the Leads CRM tab. |
| `pad/schema.sql` | **The database.** Six tables, two reporting views, triggers. See `docs/SCHEMA.md`. |
| `pad/db.cjs` | The storage layer: opens the SQLite file (node:sqlite), binds values safely, JSON helpers, address matching, derived counters. |
| `pad/pipeline.cjs` | The domain layer: the stage machine, what a send does to a lead, what an inbound reply does, the mail↔CRM sync. Shared by both processes so they can never disagree. |
| `pad/server.cjs` | The pad process (`:3001`): sending, drafts, the Resend webhook, and the Leads tab's `/api/crm/*` proxy. |
| `pad/leads/server.cjs` | The CRM API (`:3002`): routing, auth, rate limiting over `pipeline.cjs`. |
| `pad/outreach-links.cjs` | Send-time link internalisation: refuses to send a draft whose tracking links are external or stale, re-mints them first (fail-closed). |
| `pad/tools/` | `migrate-json.cjs` (JSON → SQLite, one-way, idempotent-guarded), `db-snapshot.cjs` (consistent copy via VACUUM INTO), `db-report.cjs` (the readable state), the e2e suite + runner, `cutover-to-sqlite.sh`. |
| `attribution/` | `nf-attribution` — token store, click route and visit middleware. `utils/generate-links.js` mints links, `utils/export-visits.js` reports per-lead visits. |
| `scripts/` | Pipeline glue: `outreach_internalize.py` (rewrite drafts to internal tokens), `outreach_intro_homepage.py` (per-lead homepage signature token), `apollo_enrich.py` (enrichment), `snapshot-state.sh`. |
| `skills/research/` | The agent skills that run the pipeline: `sponsor-outreach-pipeline`, `outreach-attribution`, `email-attribution-tracking`, `email-follow-up-intel`, `contact-discovery`, `newsletter-market-intelligence`, `competitor-news-monitor`. |
| `artifacts/` | Output of real runs: sponsor verification + legitimacy classification, Hunter contact pulls, the lead tracking sheet, the per-lead email batch, the weekly outreach plan, follow-up intel. |
| `docs/` | `END-TO-END.md` (operating manual), `SCHEMA.md` (the data model and why each field exists), `STATE.md` (current pipeline state, regenerated on every snapshot). |

## Running it

Requirements: Node with `node:sqlite` (Node 22.5+; this box runs v26). No npm
dependencies — the pad and the engine are stdlib-only.

```bash
cd pad
cp .env.example .env      # then fill in the values
./boot.sh                 # pad on 127.0.0.1:3001; the leads engine on 3002:
./leads/boot.sh
```

Required env (see `pad/.env.example`): `RESEND_API_KEY`, `PAD_TOKEN`,
`RESEND_WEBHOOK_SECRET`, `BRAND_NAME`, `FROM_EMAIL`, `PUBLIC_BASE_URL`,
`PAD_DOMAINS`, plus `NEWSLETTERFIT_API` + `API_BEARER_TOKEN` (read from
`/home/boxed/.config/newsletterfit/corpus.env` by `boot.sh`) for link re-minting.

`PAD_DOMAINS` matters whenever the Resend account is shared: Resend's Sent and
Receiving APIs are account-wide, so without it the inbox shows every other brand's
mail. Empty means no filtering, which is right for a single-brand account.

The database lives at `pad/data/outreach.db`. Override with `OUTREACH_DB` — but
**do not leave it set** in a shell that starts the live services: a stray value
points production at a throwaway file. `tools/cutover-to-sqlite.sh` unsets it for
exactly that reason.

```
node tools/db-report.cjs                 # read the state
node tools/db-report.cjs --lead mcalvany # one lead's timeline
node tools/db-snapshot.cjs /tmp/copy.db  # consistent copy, WAL included
```

## The stage machine

Stages, colours and follow-up cadence are data, not code — override them with
`pad/leads/config.json` (copy `config.example.json`), `LEAD_STAGES` /
`LEAD_DUE_DAYS`, or `LEADPAD_CONFIG` pointing at a shared file.

```
Leads -> First Email -> Follow-up 1 -> 2 -> 3 -> 4 -> Qualified
                                                    Replied / Won / No / Archived
                                                        (terminal, never auto-advanced)
```

Rules the engine enforces (in `pad/pipeline.cjs`, verified by the e2e suite):

- A sponsor sits in **Leads** only until its first email is actually *sent*. A
  draft sitting in the pad is still Leads.
- A successful send advances the lead **one** stage: `NEXT_STAGE[stage]`. Leads
  therefore lands on First Email exactly; a lead in Follow-up 2 moves to
  Follow-up 3. The move is written to `lead_stage_events` with `by='send'`.
- Every sent message records `stage_at_send` — the stage **frozen** at that moment.
  Moving the lead later never rewrites what the history says.
- `emails.resend_id` is UNIQUE, so recording the same send twice (the pad and the
  CRM both see it) is a no-op and can never double-advance a stage.
- The recipient must match a lead (`email` or any address in `emails`) — emailing
  an address the CRM does not own sends mail but moves nobody.
- **Terminal stages** (Replied / Won / No / Archived) are never auto-advanced; a
  human PATCH moves them.
- `POST /api/sync` is the backstop: it logs pad mail into the timeline, self-heals
  any lead that still says Leads but has outbound mail on record, and flags
  replies. A reply moves a non-terminal lead to **Replied**.
- `converted` is separate from `stage`: a deal can close without rewriting the
  pipeline.

## Reporting

Two views carry the reporting, plus one for the cadence:

- `v_lead_pipeline` — per lead: stage, converted, emails_sent, replies,
  last_reply_at, days_since_contact, what is due next.
- `v_lead_timeline` — emails, replies and stage changes in one chronological
  stream (this is what the lead detail panel renders).
- `v_followups_due` — who is due a touch now, by the day 3/7/14 cadence.

Exposed over HTTP as `GET /api/pipeline`, `GET /api/leads/:id/timeline` and
`GET /api/followups-due`.

## State

This repo versions the runtime state on purpose: `pad/data/outreach.db` (leads,
stage history, every message, replies, drafts, audit events) and `docs/STATE.md`,
the readable rendering of it. That is deliberate — Resend retains email content
and metadata for **30 days on every plan**, and this database is the only off-box
archive of the CRM. Commit the snapshot after meaningful runs:

```bash
./scripts/snapshot-state.sh            # code + a consistent DB copy + STATE.md
./scripts/snapshot-state.sh --commit
```

The pre-SQLite JSON stores are kept as a rollback at
`/home/boxed/resend-pad/attic/pre-sqlite-*/` (and the migration never deletes
them).

## Attribution rules that are policy, not preference

- Every outreach link is **internal**: `dest` must start with
  `https://newsletterfit.com/app/publications/<slug>`. Never send a lead to
  substack.com or any external site.
- Pasted links carry only the tracking token
  (`https://newsletterfit.com/api/click?lt=<TOKEN>`) — no UTM, no `dest=` param,
  no external URL, so it does not read as an open redirect.
- Resolve the canonical slug from the search API first; never invent a subscriber
  number, a publication or a placement. Proof-first means the metadata proves the
  ad, not the other way around.

## License

Private / proprietary — NewsletterFIT.
