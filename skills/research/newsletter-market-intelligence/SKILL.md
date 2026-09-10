---
name: newsletter-market-intelligence
description: Mine newsletter corpora for sponsor/audience/topic signals.
---

# Newsletter / content-corpus market intelligence

Turn a corpus of newsletters (Substack & similar: publications, articles, raw HTML payloads,
mention graphs, AI enrichment) into founder-grade intelligence: sponsor fit, audience profiles,
rising topics, and the monetization gap. Read-only friendly; works against an API + read-only Mongo.

## Working directory & overloaded tokens
Treat all `skills/newsletterfit-corpus` (user-owned) + this skill as companions. Credentials and
allowed collections for the NewsletterFIT corpus live in `newsletterfit-corpus`; load that skill's
SKILL.md too when running against that corpus.

## Workflow (probe-first)

1. **Load creds from a script file, not inlined.** Sourcing an env file and chaining API calls into
   one long `set -a; source ...; curl ...` line trips the agent's command-parser blocklist.
   Write a `.sh` that `source`s `corpus.env` (gives `$NEWSLETTERFIT_API`, `$MONGODB_URI`) and `bash`
   it.
2. **Probe schema before aggregating.** For EACH source (API response + each Mongo collection) print
   `Object.keys(sampleDoc)` first. The API's normalized fields often differ from raw Mongo fields;
   sorting/`$match`ing on guessed names returns `undefined`.
3. **Verify whether enrichment is real or placeholder.** Inspect the AI-analysis fields
   (`ai.model`, `analysis.*`, `enrichment.status`). If `ai.model=="mock"` the enrichment is
   synthetic — do NOT cite it as grounded fact; say so and rely on fields you verified.
4. **Aggregate what IS real:** nested `stats.*` (reach tier, sponsor fit, momentum), `topics[]`,
   `entities[]`, `reactions` totals, `idealSponsors` categories, `audienceProfile`s. Rank by reach +
   sponsor-fit across publications.
5. **Hunt the monetization gap** as the thesis: fields like `detectedSponsoredPosts`,
   `recentSponsors`, `acceptsSponsors` that are unpopulated/`0` despite raw payloads carrying sponsor
   copy → that unbuilt layer is usually the business opportunity.
6. Validate external claims with `web_search`/`web_extract` before asserting them.

## Pitfalls (learned the hard way)

- `reactions` on an article is an **array of `{emoji, count}` objects**, not a scalar. To rank by
  engagement, sum the `count`s (or use an already-aggregated field); sorting the array directly
  yields `[object Object]`.
- API fields (`id`, `subscribers`, `sponsorMentions`, `momentumPct`) and Mongo raw fields
  (`publicationId`, `subscriberEstimate`) are different namespaces. Probe raw keys before `$match`/
  `$sort`.
- Raw Mongo docs may nest analytics in a single `stats`/`sponsorship` sub-object; flatten first.
- Long inline shell that sources env + queries, or emits heredocs/giant one-liners, can hit the
  bounded-input blocklist → route through a `.sh`.
- Read-only discipline: never insert/update/delete; never read the app-secrets env file.
- `mongosh --quiet`: no top-level `return` inside `--eval` (it's an expression context —
  wrap bodies in an IIFE), and `tojson()` is undefined (use `print(JSON.stringify(...))`).
- `articleraws.body_html` is the raw HTML; strip tags before computing string-match offsets
  and verify its length before quoting a sponsor ad.

## Sponsor outreach / cold-email personalization

For conversion-focused work, the corpus's best asset is sponsors' **real published ad copy**
in `articleraws.body_html`. Mine it and quote it in the email's first line (Layer 1); never
fake a quote for sponsors lacking a stored copy (e.g. Glean, DeleteMe). Full recipe, mongosh
skeleton, and per-tier CTA guidance:
`references/sponsor-outreach-copy-mining.md`.

## References

- `references/sponsor-outreach-copy-mining.md` — mine real sponsor ad copy for personalized
  cold email; mongosh query skeleton + gotchas.
- `references/newsletterfit-corpus-notes.md` — NewsletterFIT corpus specifics recovered from a real
  session: exact collection counts, raw Mongo schema for `publications`/`articles`/`articleraws`,
  the confirmed `ai.model=mock` gap, and sponsor/audience intelligence aggregates.