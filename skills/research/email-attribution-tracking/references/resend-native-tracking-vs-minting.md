# Resend-native tracking vs first-party token minting

Decision note, researched 2026-09-10 (Resend custom-tracking-domains launch docs +
live account inspection). Re-verify plan gating and per-domain flags before
proposing a switch — tracking availability and free-tier details move.

## What Resend actually offers

- **Open tracking** — inserts a 1x1 transparent GIF; the load records an open.
- **Click tracking** — rewrites hyperlinks in HTML email to route through a
  tracking subdomain, records the click, then redirects to the final URL.
- **Custom tracking domains** — your own branded subdomain (e.g.
  `links.emails.example.com`) instead of a shared tracking domain, added via a
  **CNAME**; free for all Resend users. If the domain has CAA records you must add
  Resend's CAA record too, so a TLS cert can be issued for the tracking subdomain.
- **Webhooks** — `email.opened` / `email.clicked` (alongside sent/delivered/
  bounced/received), so engagement data can be written to your own database.

Enabled state is **per domain** and **off by default**. Turn it on in the
dashboard (Domains → Configuration → "Enable tracking metrics") or via API:
`domains.create` / `domains.update` with `open_tracking`, `click_tracking`,
`tracking_subdomain`. The API docs note click tracking "is only applied if a
`tracking_subdomain` is configured and verified".

**The provider's own guidance:** enable open/click tracking for **Broadcasts
only**. For transactional mail they advise against it — a tracking pixel and
rewritten links make inbox providers more likely to classify the mail as
marketing. 1:1 outreach sent from a composer sits much closer to transactional
than to a broadcast list.

## Verified account state on this stack (2026-09-10)

| Domain | Status | Region | Open tracking | Click tracking |
|---|---|---|---|---|
| newsletterfit.com | verified, send+receive | eu-west-1 | false | false |
| starterlens.com | verified, send+receive | eu-west-1 | false | false |

Inspect with the Resend tools (`list_domains`, then `get_domain` for DNS records)
rather than assuming. The pad's webhook archive (`data/webhooks.jsonl`) held 21
events, **all `email.received`** — i.e. the receiver exists (Svix-verified), but no
engagement events are subscribed yet. Adding `email.opened` / `email.clicked` is a
subscription change, not new infrastructure.

## Why it is easier — and what it does not answer

Easier/cleaner (real, and the part users are right about):

- delete the minting call, the token store + dedupe mirror, the token-format
  discriminator, the dead-token re-mint path, the `/api/click` handler, and the
  `AttributionStore`-loads-its-file-once-at-startup pitfall;
- fewer moving parts, nothing to migrate when a token format changes.

What it cannot give you — the reason not to replace the token layer:

- **No lead identity.** A click event identifies an email/message, not which lead
  opened it, unless you separately tag sends and confirm those tags come back on the
  engagement events. Verify that before betting attribution on it.
- **No post-click behaviour.** The moment the redirect lands on your site, Resend's
  knowledge ends. Repeat visits, pages viewed, homepage vs pricing — the token +
  site-middleware pattern exists precisely for that window.
- **Opens are not a signal.** Apple Mail Privacy Protection prefetches the pixel
  (often near-100% "opens" for those recipients), corporate scanners cache it, and
  many clients block images by default. Closest to unusable for decision-making.
- **Phantom clicks either way.** URL scanners (SafeLinks, Proofpoint) follow links,
  so both systems log prefetch clicks. Neither dedupes for you — filter on
  user-agent/timing if it matters.

## Trust and deliverability

- A **branded** tracking subdomain is unobtrusive and protects sender reputation
  versus a shared tracking domain (where other senders' reputation bleeds onto
  yours). Use it if you enable tracking at all.
- The **shared/default** tracking domain puts an unfamiliar third-party redirect in
  a cold B2B email. That reads as tracking to recipients and gets followed and
  flagged by corporate URL scanners — expect suppressed click-through. Never use it
  for outreach.
- Links that point **straight at your own domain** (what the token pattern gives
  you) are still the cleanest possible presentation. A tracking subdomain is a small
  step backwards in appearance for a real data gain.

## How the two interact if you run both

Click tracking rewrites links **server-side at send time**, so a pad-side gate that
validates the *submitted* HTML still sees your own-domain token URLs and passes; the
rewrite happens after submission. Both systems then observe the same click (Resend
first, then your `/api/click` after the redirect) — workable, but **dedupe when
merging** counts.

Keep or drop the send-time integrity gate deliberately: today it blocks sends whose
links point off-domain or carry unknown tokens. Dropping minting drops that check
too — a mistake it currently catches becomes a silent bad link.

## Recommendation

Hybrid, not replacement: enable **click tracking on a branded subdomain** as a
redundant, zero-maintenance feed and a cross-check against your own numbers; keep
token links authoritative for per-lead attribution and post-click behaviour. Leave
**open tracking off** for the outreach domain.

Pilot the switch on a low-stakes domain (a freshly added brand domain) rather than
the domain your live outreach sends from, and remember both endpoints of the
trade-off move: re-read the per-domain flags before promising anything.
