# The data model

SQLite via `node:sqlite` (`DatabaseSync`), one file: `pad/data/outreach.db`.
Schema: `pad/schema.sql`. Storage layer: `pad/db.cjs`. Domain layer:
`pad/pipeline.cjs`.

This schema started from a generic CRM outline and was adapted for NewsletterFIT
rather than copied. What that means, honestly, field by field:

## Kept as-is

`id`, `company`, `domain`, `contact_name`, `email`, `emails` (JSON array of every
known address), `source`, `stage`, `stage_changed_at`, `priority`, `score`,
`owner`, `tags`, `notes`, `meta` (JSON escape hatch), `converted`, `converted_at`,
`value_cents`, `currency`, `unsubscribed`, `bounced`, `first_contact_at`,
`last_contact_at`, `next_follow_up_at`, `created_at`, `updated_at`, `archived_at`,
`deleted_at`.

## Removed, renamed, added

| Change | Why |
| --- | --- |
| **dropped `phone`** | This pipeline is email-only outbound. Nothing in it ever calls a sponsor: Hunter returns addresses, the pad sends mail, replies arrive as mail. A phone column would be a promise the pipeline cannot keep. |
| **dropped `niche` → added `industry`** | "Niche" is a content-marketing word. What matters here is the vertical a sponsor's sponsored publications cover, because that is what gets matched against the corpus lookalikes. |
| `website` kept, `domain` treated as the key | `domain` is what Hunter needs and what address matching leans on; `website` is only a URL. |
| `contact_title` + `contact_role` both kept | The targeting rule (GTM/growth first, then partnerships, marketing, CRO, founder) needs the raw title *and* the normalised role. |
| `quality` → `priority` | One triage field instead of two words for the same idea. The migration maps HIGH/MEDIUM/LOW → high/medium/low. |
| `next_action_at` → `next_follow_up_at` | This pipeline's whole cadence is follow-ups; name it that. Both names are accepted on the API. |
| **added `campaign`** | Leads arrive in batches (`sep2-2026`). Nothing was trackable by batch before. |
| **added `sponsored_pubs`** (JSON array) | The product's core evidence: where we actually SAW the brand sponsoring. Proof-first means this is the first thing you look at. |
| **added `recommended_pubs`** (JSON array) | The lookalikes pitched to them. Keeping them lets you see what was promised vs what happened. |
| **added `angle`** | The pitch angle chosen for that lead. |
| **added `subscriber_range`** | The audience size of the placement that qualified them. |
| `notes` kept as JSON **and** mirrored into `events` | The old array survives in the column; every new note is an `events` row, so the audit trail is one shape. |
| `meta` also stores `placements`, `alternates`, `pick_basis`, `blocked` and `original` | The escape hatch does real work: `original` is the untouched pre-migration record, so nothing was lost in translation. |

## Stages

The generic outline (`new → contacted → replied → qualified/won/lost/archived`)
was never this pipeline's vocabulary, and the live pad UI, the cron intake job and
the agent skills are all keyed to the real ones. So the NF stages stay, with the
two genuinely missing ones added:

```
Leads -> First Email -> Follow-up 1 -> 2 -> 3 -> 4 -> Qualified
                                  Replied / Won / No / Archived   (terminal)
```

Mapping for anyone reading the generic list: `new` = Leads, `contacted` =
First Email, `lost` = No, `archived` = Archived. `qualified` is new (interested,
shaping a deal, not yet a stage of its own before) — and `replied` keeps its old
meaning as a terminal stage a send can never leave.

**`converted` is deliberately not a stage.** A sponsor can close a deal (or be
marked converted for reporting) without the pipeline pretending it moved. That is
what the generic outline asked for and it is right: `stage` answers "where is the
outreach", `converted` answers "did money change hands".

## Tables

| Table | Holds | Notes |
| --- | --- | --- |
| `leads` | one row per sponsor | JSON columns: `emails`, `tags`, `notes`, `sponsored_pubs`, `recommended_pubs`, `meta`. Queryable with `json_each()` / `json_extract()`. |
| `lead_stage_events` | every transition: `from_stage`, `to_stage`, `at`, `by`, `note`, `source` | The stage *history*, not just the current stage. A birth event is inserted by trigger for every new lead. |
| `emails` | outbound **and** inbound, one row per message | `direction`, `stage_at_send` (frozen), `thread_id`, `parent_email_id`, `in_reply_to`, `from_addr`/`to_addr`/`cc`/`bcc`/`reply_to`, `subject`, `body_text`, `body_html`, `template_id`, `campaign`, `resend_id` (UNIQUE), `status`, `status_at`, `error`, `sent_at`. |
| `replies` | inbound, with the interactive bits | `lead_id`, `email_id`, text + html, `message_id` (UNIQUE when set), `in_reply_to`, `received_at`, `classification`, `sentiment`, `is_read`, `starred`, `deleted_at` (the ✕ — a soft delete), `raw` (the verbatim Resend payload). |
| `drafts` | the review-before-send queue | `status` = `draft` / `sent` / `discarded`. A sent or discarded draft is **kept**, not spliced out of a list, so the history survives. |
| `events` | the generic audit trail | `entity`, `entity_id`, `type`, `payload`, `at`, `actor`. Webhooks, notes, sends, syncs, draft actions, clicks. |

Threading: a lead's first outbound message becomes the thread root
(`thread_id = 'thread:<id>'`) and later sends point at their predecessor through
`parent_email_id`, so a follow-up sequence is reconstructable.

## Views

| View | One row per | Columns |
| --- | --- | --- |
| `v_lead_pipeline` | live lead | stage, converted, priority, score, campaign, `emails_sent`, `replies`, `last_reply_at`, `days_since_contact`, contact stamps, next follow-up, deal value |
| `v_lead_timeline` | event | `kind` (`email` / `reply` / `stage`), `ref`, `at`, `summary`, `detail` — the three sources unioned and ordered |
| `v_followups_due` | lead due a touch | the pipeline row plus `days_overdue`, excluding terminal stages |

## Triggers

- `trg_leads_stage_changed_at` — keeps `stage_changed_at` honest even if someone
  runs a hand-written `UPDATE` in the sqlite shell.
- `trg_leads_birth_stage_event` — every new lead gets its first stage event, so no
  lead ever has an empty history.

## Strictness

Every table is `STRICT`: storing `'ninety'` in `score` is an error, not a silent
coercion. Booleans are 0/1 integers. All timestamps are ISO-8601 UTC strings, so
they sort as text and compare with `julianday()` when arithmetic is needed.

## Where the old JSON went

| Was | Now |
| --- | --- |
| `leads/data/crm.json` → `leads[]` | `leads` (+ `meta.original` holds the untouched record) |
| `leads/data/crm.json` → `activity[]` kind `stage` | `lead_stage_events` |
| `leads/data/crm.json` → `activity[]` kind `note` | `events` (entity `lead`, type `note`) |
| `leads/data/crm.json` → `activity[]` kind `email_out` | `emails` (direction `outbound`) |
| `leads/data/crm.json` → `activity[]` kind `email_in` | `emails` (inbound) + `replies` |
| `leads/data/crm.json` → `sync` | `events` (entity `system`, type `sync`) |
| `data/drafts.json` | `drafts` (status `draft`) |
| `data/sent-drafts.jsonl` | `drafts` (status `sent`) + `emails` (enriched with the true `sent_at` and `resend_id`) |
| `data/webhooks.jsonl` | `events` (verbatim payload) + `emails.status` + `replies` |

**Never deleted.** The migration reads the JSON and leaves it in place; the live
cutover also copies the whole pre-SQLite state and the old server code to
`/home/boxed/resend-pad/attic/pre-sqlite-<stamp>/` as the rollback path.

## Migration honesty

Three things the migration refuses to fake, and reports instead:

- Imported rows whose source literally says "no send timestamp" keep
  `sent_at = NULL`. An import time is never promoted into a send time.
- `stage_at_send` for pre-CRM mail is reconstructed from the recorded stage
  history when it exists, otherwise taken from the lead's current stage (which was
  itself derived from that send at import time). No transition is invented.
- Webhook events belonging to another brand on the shared Resend account are
  logged to `events` but **not** imported as CRM mail — the same `PAD_DOMAINS`
  gate the pad applies to the live inbox. In the first migration that was 28 of 43
  archived events (StarterLens and garage-door outreach on the same account).
