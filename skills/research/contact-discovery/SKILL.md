---
name: contact-discovery
description: Find the person/email to pitch for a company via Hunter.io.
---

# Contact Discovery for Outreach

Turns a company name (or a ranked list of outreach targets) into a specific decision-maker's
email — the growth / partnerships / marketing head appropriate for the pitch (e.g. "who do I
email about sponsoring a newsletter").

The primary tool is **Hunter.io's v2 API** (domain-search endpoint). Do NOT burn credits blindly:
- Free plans: ~50 search credits + ~100 verification credits per month. **Important**: each successful domain-search consumes **2 credits**: 1 search credit AND 1 verification credit (so 50 searches actually yields ~25 domain-searches with email data). **Check `/v2/account` BEFORE spending** to confirm the balance and reset date, and re-check as you go.
- Anything domain-search/email-finder returns is Hunter's *inferred* address with a `confidence` (80-99). Treat it as a strong lead — spot-check deliverability before an actual blast.

## Which endpoint does what (pick the RIGHT one — they cost differently)
- `GET /v2/domain-search?company=X&type=personal&decision_maker=true&limit=10` — **THE one to use.** Returns the organization's people + emails; `decision_maker=true` filters to people holding buying/decision authority. One search credit per domain.
- `GET /v2/discover` — the endpoint people *think* they want, but it returns **companies matching a profile, NOT people**, and it's free. It's a company-prospecting step, not contact-finding. Skip unless finding *companies* not emails.
- `GET /v2/email-finder` — resolve one specific person's email once you know first+last+domain. Expensive, one-off.
- `GET /v2/people/find` and `/v2/combined/find` — enrich a known email. Expensive; only after you have an address.
So **for "give me the decision-maker contact for company Y" the efficient call is ONE domain-search** filtered to decision-makers. Full endpoint + credit table in `references/hunter-api.md`.

## Workflow
1. **Confirm a live key + budget**: `GET /v2/account?api_key=KEY`, read `data.requests.searches.remaining` / `verifications.remaining`. Free-plan gotcha: keep `limit=10` (asking for more → `pagination_error`, HTTP 400).
2. **Resolve the domain.** You can pass `company=<name>` and Hunter resolves it, but **short/ambiguous names auto-resolve to the WRONG company** — real failures: Imagine→imagine.io (3D viz not the AI firm), AWS→aws.ac.th (Thai school), Unblocked→unblocked.life, DeleteMe→deleteme.com (real: joindeleteme.com), Kiro→a Korean broadcaster. Quick web-verify ambiguous short names and pass `domain=` explicitly.
3. **Query**: `curl -s "https://api.hunter.io/v2/domain-search?domain=<domain>&type=personal&decision_maker=true&limit=10&api_key=<KEY>"`. Parse `data.domain`, `data.organization`, `data.emails[]` (value, first_name, last_name, position, department, confidence).
4. **Curate the right person** — `decision_maker` reduces noise but on big/famous companies it still skews to HR/legal/people-partner staff. Score candidates by position, preferring partnerships > growth > marketing (director/VP/Head of) > CMO/CRO > business-development > founder/CEO for tiny cos. Pull from the full candidate set, not the first row.
5. **Deliver** a CSV + readable table (`sponsor, contact_name, position, department, contact_email, domain, confidence`), sorted by sponsor. Note ambiguous/likely wrong names so the user can confirm.
6. **Stop well short** of exhausting credits. If thin, hit high-value B2B/tech targets first, leave the rest for another account/reset.

## Pitfalls
- **Wrong company auto-resolution is the #1 credit-waster.** Verify ambiguous names, pass `domain=` explicitly.
- **Free plan caps results at 10** (`limit` >10 → `pagination_error`). Set `limit=10`.
- **401 `authentication_failed`** = a mangled key in the URL. Verify the key; test with `/v2/account` first.
- **Don't `%s`-interpolate a URL that already contains `%20`** — the format token collides. Build URLs by string concatenation.
- **Pick by role, not by top row** — result order is not "best fit" order.
- **Deliverables dir may be read-only** (e.g. `/srv/newsletterfit/...` is owned by another user). Fall back to home if a write is denied.

## Support files
- `references/hunter-api.md` — endpoint/credit economics, response shape, free-plan limits.
- `references/apollo.md` — Apollo.io free-tier alternative: REST endpoints, credit economics, MCP-vs-REST (OAuth caveat), working helper path.
- `references/free-enrichment-tiers.md` — the full 2026 free landscape: Apollo/Snov/Hunter pools + Verifalia/Zerobounce/QuickEmailVerification verifier tiers + decision pattern when Hunter is dry.
- `scripts/hunter_contacts.py` — fetch decision-maker candidates for a list of targets, print + save CSV, with a credit budget guard. Re-run with your own list + key.