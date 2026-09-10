# LeadPad — an email pad with a leads CRM

Two small Node services, zero npm dependencies, one JSON store each, one token.

A plain outreach client (compose / sent / received / drafts) with a leads CRM as
the first tab: every lead in one list, colour-coded by pipeline stage, with the
email history that got it there. Built to be lifted into other projects — see
[LEADPAD.md](LEADPAD.md).

```
browser
   │  https://<host>/pad/          (Caddy: /pad/* -> 127.0.0.1:3001)
   ▼
pad  (server.cjs, index.html)      :3001   ← the only public surface
   │  ├─ Resend API (send, sent, receiving)
   │  ├─ drafts queue      data/drafts.json
   │  └─ /api/crm/*  ─────────────────────► leads engine (leads/) :3002
                                              ├─ leads + timeline  leads/data/crm.json
                                              └─ proxies mail back through the pad
```

The pad owns the UI and the only public route. The leads engine is internal: it
owns the lead store and the pipeline logic, and the pad proxies to it, injecting
the engine's credential server-side. So the user sees **one URL and one token**.

## Quick start

```bash
cp .env.example .env          # then fill it in — see the file for every knob
./boot.sh                     # pad on :3001
./leads/boot.sh               # leads engine on :3002
```

Open `http://127.0.0.1:3001/`, paste `PAD_TOKEN`, and you are in. The token is
remembered in the browser afterwards.

## What you get

- **Leads tab** — table of leads with stage pill, contact + role badge, last
  email, next follow-up (overdue first), tracked-link clicks. Filter chips per
  stage, live search. Click a row for contact, notes, stage dropdown, the full
  activity timeline, and "email this lead" (jumps to Compose pre-filled).
- **Email tabs** — Compose (with drafts), Sent, Received, read/reply in a modal.
- **Automatic coordination** — sending from Compose logs the email against the
  matching lead and advances its stage; "Sync email" backfills anything the CRM
  has not seen and moves a lead to *Replied* when it finds an inbound reply.
- **Brand-safe mail lists** — `PAD_DOMAINS` filters Sent/Received to this
  brand's addresses, because a Resend account is shared across brands and its
  APIs return every brand's mail. The UI says how many were hidden.
- **Deployment glue** — `boot.sh` / `watchdog.sh` for each service (crontab
  `*/1 * * * *`), Caddy route helper in `deploy/`; `tests/` holds the
  browser-less UI render test.

## Configuration

Everything project-shaped is env-driven; nothing needs code edits:

| variable | what it does |
|---|---|
| `PORT`, `PAD_TOKEN` | pad port and the single access token |
| `RESEND_API_KEY`, `RESEND_WEBHOOK_SECRET` | Resend credentials (server-side only) |
| `BRAND_NAME`, `FROM_EMAIL`, `PUBLIC_BASE_URL` | branding + sender, served via `GET /api/config` |
| `PAD_DOMAINS` | comma-separated domains kept in Sent/Received; empty = no filtering |
| `CRM_PORT`, `CRM_HOST`, `PAD_URL` | leads engine wiring |
| `DATA_DIR`, `SERVICE_NAME` | leads store location and log identity |
| `LEAD_STAGES`, `LEAD_DUE_DAYS`, `LEADPAD_CONFIG` | pipeline shape (see `leads/config.example.json`) |

## API

Pad (token: `X-Pad-Token`):

```
GET  /api/health                     no auth — used by the watchdogs
GET  /api/config                     no auth, no secrets — branding for the UI
GET  /api/domains                    verified Resend sender domains
POST /api/send                       send, with send-time link internalization
GET  /api/sent | /api/received | /api/archive
GET  /api/drafts   POST /api/drafts/:id/send   PUT|DELETE /api/drafts/:id
POST /api/webhook                    Resend events (Svix signature)
ANY  /api/crm/*                      proxied to the leads engine
```

Leads engine (token: `X-CRM-Token`; the pad injects it):

```
GET   /api/health
GET   /api/meta                      brand + stages + per-stage counts
GET   /api/leads[?stage=]            leads with derived last-touch / next-due
GET   /api/leads/:id                 one lead + activity timeline
POST  /api/leads                     create
PATCH /api/leads/:id                 stage / contact / notes / next_action
POST  /api/leads/:id/note            append a note
POST  /api/sync                      log pad sent+inbox mail against leads
GET   /api/email/inbox|sent|drafts    proxied to the pad
POST  /api/email/send | /api/email/drafts/:id/send
```

## Operations notes

- **Watchdogs must not collide.** Both services historically ran as
  `node server.cjs`, until the pad's `pkill -f 'node server.cjs'` started killing
  the leads engine on every pad restart. The engine now runs as
  `node server.cjs --leads-engine` and both watchdogs kill by port owner first.
  Give any new sibling service its own marker too.
- **Editing `index.html`?** The server re-reads it per request, so UI changes are
  live immediately — and the pad's client JS is never linted by tooling. Extract
  the inline script and `node --check` it before reloading, or one nested quote
  takes the whole UI down.
- **Rate limiting** is per-IP on `/api/*`; the pad caps at 60/min.

## Project-specific parts

`outreach-links.cjs` implements NewsletterFIT's send-time link internalization:
every tracked link must be a server-minted token on the brand's own domain, and
any external URL blocks the send. Reuse it as-is if you have that corpus API,
otherwise drop the module and the `require` in `server.cjs`, and configure
`ATTRIBUTION_STORE` / `NEWSLETTERFIT_API` accordingly.
