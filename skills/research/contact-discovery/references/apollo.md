# Apollo.io enrichment — free-tier reality check (validated 2026-09-05 by live API calls)

**Headline: the Apollo FREE plan exposes NO useful REST data endpoints.** The key validates
(`/v1/auth/health` → 200 `{"healthy":true}`) but every data call 403s with
`API_INACCESSIBLE` ("The api/v1/... API is not included in your Free plan"). Free value lives
in the WEB UI and Chrome extension, not the API. Do not build agent automation on free Apollo.

## Auth & hosts (verified)
- Header: `X-Api-Key: <key>`. Bearer token → 401 (that's OAuth-partner-only).
- Data endpoints live under **`https://app.apollo.io/api/v1/`**; `https://api.apollo.io` serves
  only the health/profile-style endpoints.
- **`app.apollo.io` is Cloudflare-blocked from datacenter/VPS IPs** — `curl` returns
  `HTTP 403 error code: 1010`. `api.apollo.io` works from a VPS but has no data endpoints.

## Free-plan API gates (live-tested this session)
| Endpoint | Result on free plan |
|---|---|
| `POST /v1/people/match` (enrichment) | **403 `API_INACCESSIBLE`** |
| `POST /v1/mixed_people/search` | **403 `API_INACCESSIBLE`** |
| `GET /v1/users/api_profile` / `v1/auth/health` | 200 (auth-health only) |

## Official MCP
- `https://mcp.apollo.io/mcp` — OAuth 2.0 browser login ONLY. Cannot complete headless
  (`hermes mcp login apollo` needs an interactive TTY; the CLI errors non-interactively).
- Scales with the account's plan/credits; free = same web-UI limits.

## What the free plan IS good for (browser, not API)
- People search UI: ~75 email reveals/month (~900/yr), 1 credit per email/phone reveal.
- Chrome extension for LinkedIn/company-site contact pulls.
- ~10 exports/mo; AI research ~100 runs/mo. Credits do not roll over.

## Plan
- Free account → use the WEB UI manually for reveals; don't wire the REST helper.
- Upgrade (Basic ~$49/user/mo annual, 30k credits/yr) BEFORE scripting enrichment.
- Keep `/home/boxed/apollo_enrich.py` for a future paid key (BASE `https://app.apollo.io/api/v1`,
  `X-Api-Key` header). On free it 403s — don't misread that as a key problem.
- When Hunter is dry, prefer: agent web-research (unlimited, free) + Snov (manual web) +
  Verifalia 25/day for verification (see free-enrichment-tiers.md).