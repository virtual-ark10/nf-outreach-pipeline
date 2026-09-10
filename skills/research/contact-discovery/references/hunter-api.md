# Hunter.io v2 API — endpoint & credit economics (checked 2026-08-26)

Base: `https://api.hunter.io/v2`. Auth: `?api_key=KEY`. Any one of the 3 auth styles works;
`api_key` query param is simplest.

## Credit model (Free plan)
- ~50 **search** credits/month + ~100 **verification** credits/month. Reset date in `/v2/account`.
- A **new query is counted only if it returns at least one result.**
- Free plans cap **Domain Search results at 10** per call (any `limit` > 10 → HTTP 400 `pagination_error`).
- Discover / Domain Expore are **free** (they return companies/counts, not people).

## Endpoints (use the right one — cost differs)
| Endpoint | What it returns | Cost | Notes |
|---|---|---|---|
| `GET /v2/account` | plan, used/remaining search+verify credits, reset date | free | ALWAYS call first |
| `GET /v2/discover` | **companies** matching a profile (domain, org, headcount, email counts) | free | Find *companies*, not people |
| `GET /v2/domain-search` | **people + emails for one domain** | 1 search credit | THE contact-finder |
| `GET /v2/email-finder` | best email for a known first+last+domain | 1 search | One-off single-person lookup |
| `GET /v2/people/find` | person enrichment from a known email | 1 search | After you have the address |
| `GET /v2/combined/find` | person + company enrichment from an email | ~2 search | Most expensive; rarely needed |
| `GET /v2/email-verifier` | deliverability check of an address | 1 verification | Use before a blast |

## The `domain-search` request that works
```
curl -s "https://api.hunter.io/v2/domain-search?domain=<domain>&type=personal&decision_maker=true&limit=10&api_key=<KEY>"
```
- `company=<name>` (instead of `domain=`) makes Hunter resolve the domain itself — handy, but **auto-resolution is unreliable for short/ambiguous names**.
- `type=personal` (drop generic/info@).
- `decision_maker=true` → only people likely to hold buying authority (seniority + department).
- `limit=10` is the free-plan max; higher → `pagination_error`.

### Response shape
```json
{
  "data": {
    "domain": "ahrefs.com", "organization": "Ahrefs", "pattern": "{first}.{last}@",
    "emails": [ {
      "value": "igor.gorbenko@ahrefs.com",
      "first_name": "Igor", "last_name": "Gorbenko",
      "position": "Director of Partnerships", "department": "management",
      "seniority": "senior", "confidence": 96, "verification": {...}
    } ]
  },
  "meta": { "results": 10, "limit": 10, "params": {...} }
}
```
`data.emails[]` may be empty (no decision-makers for that domain) — that's a valid free response, not an error.

## Errors
- `401 authentication_failed` — key mangled/absent. Confirm with `/v2/account` before anything.
- `400 pagination_error` — free plan caps `limit=10`; lower your limit.
- `404` HTML page — you hit a non-existent route (e.g. a `/discover?domain=...&position=...` guess). Discover is a POST-style / filter-based endpoint, not a position-search.

## Known wrong auto-resolutions (recorded so you don't re-burn credits)
- Imagine → `imagine.io` (3D+AI viz, NOT an AI-copy firm)
- AWS → `aws.ac.th` (a Thai school)
- Unblocked → `unblocked.life` (real dev-context product: `getunblocked.com`)
- DeleteMe → `deleteme.com` (real: `joindeleteme.com`)
- Kiro → `kiro.re.kr` (a Korean broadcaster)
- Granola → auto-resolved to `granola.so`; the AI-notepad product lives at `granola.ai`
- Rep / Anam / Profound → resolved to unrelated or regional firms; verify before trusting org identity

Rule: when the org name is a short common noun/brand (Imagine, Rep, Cyber, Turn, Rich, Town, Delta, Scan, Cello), confirm the intended product/domain with a quick web search before spending a credit, and pass `domain=` explicitly.