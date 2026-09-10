# nf-outreach-pipeline

The NewsletterFIT sponsor outreach pipeline, end to end, in one repo: the CRM that
tracks every lead and its stage, the pad that sends and receives the mail, the
attribution package that makes every link in every email measurable, the scripts
that wire those together, and the agent skills that describe how to run all of it.

The product this pipeline sells: *we find newsletters similar to the ones you
already sponsor, using observed behavior rather than subscriber-count vanity.*

Assembled 2026-09-10 from the live deployment on the NewsletterFIT box.
Provenance: `pad/` and `pad/leads/` from `/home/boxed/resend-pad`,
`attribution/` from `/home/boxed/newsletterfit/attribution`, `skills/` from
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
                        CRM auto-advances the stage
                        Leads -> First Email -> Follow-up 1..4
                                  |
                    [5] replies + clicks land back in the CRM
                        (sync detects replies -> Replied; tokens -> visits)
                                  |
                    [6] follow-up intel per due touch (day 3 / 7 / 14)
                        corpus + last30days + web, never invented
```

## What's in here

| Path | What it is |
| --- | --- |
| `pad/` | The sending/receiving pad (zero-dependency Node). Server-side Resend key, token-gated API, inbox/sent/drafts, webhook archive, and the Leads CRM tab. |
| `pad/leads/` | The leads engine (the CRM). Separate process on `127.0.0.1:3002`, proxied by the pad at `/api/crm/*` so one token unlocks both surfaces. |
| `pad/outreach-links.cjs` | Send-time link internalisation: refuses to send a draft whose tracking links are external or stale, re-mints them first (fail-closed). |
| `attribution/` | `nf-attribution` — token store, click route and visit middleware. `utils/generate-links.js` mints links, `utils/export-visits.js` reports per-lead visits. |
| `scripts/` | Pipeline glue: `outreach_internalize.py` (rewrite drafts to internal tokens), `outreach_intro_homepage.py` (per-lead homepage signature token), `apollo_enrich.py` (enrichment). |
| `skills/research/` | The agent skills that run the pipeline: `sponsor-outreach-pipeline`, `outreach-attribution`, `email-attribution-tracking`, `email-follow-up-intel`, `contact-discovery`, `newsletter-market-intelligence`, `competitor-news-monitor`. |
| `artifacts/` | Output of real runs: sponsor verification + legitimacy classification, Hunter contact pulls, the lead tracking sheet, the per-lead email batch, the weekly outreach plan and follow-up intel. |
| `docs/` | `END-TO-END.md` — the stage-by-stage operating manual with the exact commands. |

## Running it

```bash
cd pad
cp .env.example .env      # then fill in the values
./boot.sh                 # starts the pad on 127.0.0.1:3001 and the CRM on 3002
```

Required env (see `pad/.env.example`): `RESEND_API_KEY`, `PAD_TOKEN`,
`RESEND_WEBHOOK_SECRET`, `BRAND_NAME`, `FROM_EMAIL`, `PUBLIC_BASE_URL`,
`PAD_DOMAINS`, plus `NEWSLETTERFIT_API` + `API_BEARER_TOKEN` (read from
`/home/boxed/.config/newsletterfit/corpus.env` by `boot.sh`) for link re-minting.

`PAD_DOMAINS` matters whenever the Resend account is shared: Resend's Sent and
Receiving APIs are account-wide, so without it the inbox shows every other brand's
mail. Empty means no filtering, which is right for a single-brand account.

Nothing is hardcoded to a path — `boot.sh` derives its own directory and the leads
engine defaults to `DATA_DIR=./leads/data`. See `pad/LEADPAD.md` for the full
"reuse this in another project" recipe (it is env + config, not code).

## The stage machine

Stages, colours and follow-up cadence are data, not code — override them with
`pad/leads/config.json` (copy `config.example.json`), `LEAD_STAGES` /
`LEAD_DUE_DAYS`, or `LEADPAD_CONFIG` pointing at a shared file.

```
Leads -> First Email -> Follow-up 1 -> Follow-up 2 -> Follow-up 3 -> Follow-up 4
                                                                          |
                        Replied / Won / No  (terminal, never auto-advanced)
```

Rules the engine enforces (verified in `pad/leads/server.cjs`):

- A sponsor sits in **Leads** only until its first email is actually *sent*. A
  draft sitting in the pad is still Leads.
- A successful send advances the lead **one** stage: `NEXT_STAGE[stage]`. Leads
  therefore lands on First Email exactly; a lead in Follow-up 2 moves to
  Follow-up 3. The move is written to the activity log as `auto-advanced by send`.
- The send must return 2xx from Resend and the recipient address must match a
  lead (any of `contact_email` or `extra_emails`) — emailing an address the CRM
  does not own sends mail but moves nobody.
- **Replied / Won / No are terminal**: no send will drag a lead back out of them.
- `POST /api/sync` is the backstop. It logs pad mail into the lead timeline,
  self-heals any lead that still says Leads but has outbound mail on record, and
  flags replies (inbound mail moves a non-terminal lead to **Replied**).
- Notes: `POST /api/leads/:id/note`, stage changes: `PATCH /api/leads/:id`,
  pipeline shape + counts: `GET /api/meta`. Token: `X-CRM-Token`.

## State

This repo versions the runtime state on purpose: `pad/data/` (drafts, send log,
webhook archive) and `pad/leads/data/crm.json` (every lead, stage and activity
event). That is deliberate — Resend retains email content and metadata for **30
days on every plan**, and there is no other archive of the CRM off the box. Commit
the snapshot after meaningful runs:

```bash
./scripts/snapshot-state.sh
```

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
