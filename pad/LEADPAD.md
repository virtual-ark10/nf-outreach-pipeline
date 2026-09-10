# Reusing LeadPad in another project

The point of this checkout is that a new project means **env + config**, not code.
This is the whole recipe.

## 1. Copy the two services

```bash
cp -r resend-pad /srv/other-project/pad
cd /srv/other-project/pad
cp .env.example .env
```

Nothing hardcodes a path: `boot.sh` derives its own directory (works from a
symlink too), and the leads engine defaults to `DATA_DIR=./leads/data`.

## 2. Point it at a different Resend account

```
RESEND_API_KEY=re_...            # the new project's key
PAD_TOKEN=<long random string>   # this project's single login
BRAND_NAME=Other Thing
FROM_EMAIL=hello@otherthing.com
PUBLIC_BASE_URL=https://otherthing.com
PAD_DOMAINS=otherthing.com       # shows only this brand's mail
```

`PAD_DOMAINS` matters if the account is shared: Resend's Sent/Receiving APIs are
account-wide, so without it you will see every other brand's email in the inbox.
An empty value means "no filtering" — correct for a single-brand account.

## 3. Reshape the pipeline (optional)

Stages, colours and follow-up cadence are data. Either copy
`leads/config.example.json` to `leads/config.json`, or set `LEAD_STAGES` /
`LEAD_DUE_DAYS` as JSON in the env, or point `LEADPAD_CONFIG` at a file elsewhere
so several deployments share one definition.

Rules the engine relies on:

- the **first** stage is "not contacted yet" — a send advances out of it;
- stages marked `"terminal": true` (replied / won / no by default) end
  progression and are never auto-advanced into;
- everything between them is walked in order by `NEXT_STAGE`, so a 4-step or
  7-step pipeline needs no code change;
- `dueDays` maps a stage to the number of days before the next follow-up is due
  (`0` = no further follow-up scheduled).

## 4. Run it

```bash
./boot.sh &            # pad
./leads/boot.sh &      # leads engine (internal only)
```

For a box that must survive reboots and crashes, use the two watchdogs — each is
independent and neither touches Caddy:

```cron
@reboot     /srv/other-project/pad/boot.sh
*/1 * * * * /srv/other-project/pad/watchdog.sh
@reboot     /srv/other-project/pad/leads/boot.sh
*/1 * * * * /srv/other-project/pad/leads/watchdog.sh
```

Expose **only** the pad (see `deploy/add_caddy_route.py` for the pattern); the
leads engine stays on localhost.

## 5. What to strip per project

| piece | keep? |
|---|---|
| `server.cjs`, `index.html`, `leads/` | yes — the app |
| `outreach-links.cjs` | only if you run a link-tracking API like NewsletterFIT's. Otherwise delete it and the `require` in `server.cjs`, and drop `NEWSLETTERFIT_API` / `ATTRIBUTION_STORE`. |
| compose templates in `index.html` (`TEMPLATES`) | rewrite — they are outreach copy, not logic |
| `attic/` | deployment helpers from the first install; safe to ignore |

## Data model

One JSON store at `leads/data/crm.json`:

```json
{
  "leads": [{
    "id": "acme", "company": "Acme", "contact_name": "Jo Bloggs",
    "contact_email": "jo@acme.com", "contact_role": "gtm",
    "contact_title": "Head of GTM", "stage": "first_email",
    "next_action_due": "2026-09-14", "notes": [], "stage_changed_at": "...",
    "sponsor": { "...any extra fields you want to carry through..." }
  }],
  "activity": [{ "id": 1, "lead_id": "acme", "kind": "email_out", "at": "..." }],
  "sync": { "email": "2026-09-10T20:00:00Z" }
}
```

Extra fields on a lead are preserved but ignored by the UI — handy for carrying
project-specific data (NewsletterFIT stores corpus placements and audience
ranges there). Ids are kebab-case company slugs, which keeps them readable and
makes `POST /api/leads` idempotent: a repeat post returns `409` instead of
duplicating.

## Verifying a fresh install

A render test ships with the checkout at `tests/pad_crm_render_test.cjs`: it
drives the pad's own client script in a DOM stub against the live API, which is
how you check the UI on a box with no browser. Point it at your instance and
replace the stage-name assertions with your own before trusting it.
