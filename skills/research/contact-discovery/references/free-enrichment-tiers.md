# Free email-finder & verification tiers (2026)

When Hunter's pool is exhausted or the user won't pay, these are the free pools for B2B outreach contact-finding and verification. All verified against pricing/FAQ pages Sep 2026 — re-check before relying, since free tiers change.

## Finders (find a person's email from a company)

| Tool | Free pool | Notes |
|---|---|---|
| Apollo.io | ~900 credits/yr ≈ 75/mo, 1 credit/email reveal (WEB UI + Chrome ext only) | REST data endpoints are PAID-gated on free (403 `API_INACCESSIBLE` on people/match AND mixed_people/search); `app.apollo.io` is Cloudflare-blocked from VPS/datacenter IPs (error 1010). Use the web UI manually, or upgrade to Basic before wiring the API. MCP is OAuth-browser only. Full details: apollo.md. |
| Snov.io | 50 unified credits/mo (find=1, verify=0.5, campaign=1) | Free forever, renews monthly; LinkedIn prospect finder included; **API is paid-only** → web UI manual use. |
| Hunter.io | ~25 searches + ~50 verifications/mo | Best decision-maker accuracy, API available; pool resets monthly (this user's key resets ~25th). |
| Web research | Unlimited, free | Press kits, "partner with us" pages, LinkedIn author bios surface the right contacts — automate per-company; never runs out. |

## Verifiers (check an address you already have)

| Tool | Free pool | Notes |
|---|---|---|
| Verifalia | 25/day, every day (~750/mo) | **API included on free tier** — the one to automate as a nightly drip-verify of new leads. Unknown results free? (tier-dependent — check). |
| QuickEmailVerification | 100/day (~3k/mo) | Best pure volume; PAYG credits never expire. |
| ZeroBounce | 100/mo | Unknowns don't consume credits; credits never expire; needs business/premium domain to sign up. |
| MailboxValidator | 300/mo | Free API tier genuinely useful. |
| One-time trials | Kickbox 100, DeBounce 100, NeverBounce 1,000 | Only for a single batch burst; no renewals. |

## Zero-cost extras

- Resend/Postmark real bounces = free deliverability signal per address — log `email.bounced` webhooks.
- Catch-all caveat: even "valid" checks on catch-all domains are unreliable; budget a few soft-bounce retries.

## Decision pattern

- Need ≤75 finds/mo → Apollo alone.
- Need more → Apollo + Snov tools + Hunter surgically + agent web-research.
- Verification pipeline → Verifalia 25/day automated; anything heavier bumps to paid (MillionVerifier ~$6/10k, DeBounce from $0.00045/email).