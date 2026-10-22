# NewsletterFIT capability inventory for offer design

Verified 2026-10-22 against the running deployment (local api on 127.0.0.1:3000, public
newsletterfit.com) and the repo at /srv/newsletterfit. Every endpoint and field named here was
read out of a live response or out of the route code that serves it. Where a capability is in the
code but was not exercised (an Enterprise-only export, an admin-only view), it says so.

## The positioning this supports

NewsletterFIT is an intelligence layer over the newsletter ecosystem. It reads what newsletters
publish, through a model pass per issue, and returns the entities inside that text: companies,
people, guests, products, technologies, topics and sponsors, each attached to the publication,
the article URL and the date. On top of that it keeps a per publication audience series, so a
list reads as rising or flat rather than as a one time subscriber number.

That is a different product from a sponsor directory. The sponsor graph is one output of the
reading, not the whole asset. Say corpus of newsletters, or newsletter intelligence, and never a
count of publications or articles: the number moves every few minutes and a reader can hold you
to it.

## Delivery surfaces

REST API, /api/v1, bearer key, scopes search:read, publications:read, entities:read,
exports:write. Live routes, all verified with a 200:

- GET /search returns one federated answer rather than a list. The payload carries newsletters,
  topics, companies, people, guests, sponsors, generic entities, mentions and labels, plus a
  detected query intent (a search for "brex" comes back with intent "company"). This is the
  entity surfacing surface, and it is the one a GTM buyer recognises immediately.
- GET /publications and GET /publications/by/:slug. A card carries audience label plus basis,
  momentum percentage and window, platform, category, cadence, acceptsSponsors,
  detectedSponsoredPosts, recentSponsors, contentMix, monetization and archiveAccess.
- GET /publications/:id/intel-summary. Present, but template generated today: it returns a
  sentence such as "Recent coverage includes N indexed articles" with source "template". Do not
  sell it as AI commentary.
- GET /articles and GET /articles/:id. The single article page is the richest read: article,
  publication, engagement (likes, comments, restacks, with 7 day deltas and a trend flag when
  history allows), entities split into topics, people, companies, guests and sponsors, detected
  sponsors with evidence, and related articles with a whyRelated reason.
- GET /entities/:type and /entities/:type/:id, with eight typed kinds: company, product, person,
  technology, topic, guest, brand, sponsor. An entity page returns articleCount,
  publicationCount, totalMentions, firstMention, latestMention, mentionsThisMonth, an activity
  line ("17 to 27 mentions, last 30 days vs prior 30") and the recent articles behind it with a
  relationType. This is the account signal a GTM platform buys.
- GET /directory/sponsors and /directory/sponsors/:slug. A sponsor page returns its placements
  with the evidence string lifted from the issue, its monthly placement series, the publications
  it runs in, topic mix, similar sponsors by shared inventory, and lookalike publications ranked
  with audience label, cadence, likely audience and topics.
- GET /directory/topics, /directory/topics/:slug and /directory/rising. Rising computes momentum
  from snapshots under published rules (30 day window, minimum baseline and gain, percentage
  floor and ceiling, staleness and activity cut offs), which is the defensible version of the
  claim.
- POST /exports, Enterprise entitlement only. One snapshot of publications, articles, entities
  and links over a 14 day window, and the window is hard capped in code.

MCP, at /mcp (not /api/mcp), streamable HTTP, bearer nf_live_ or nf_test_ key. Seventeen tools
are registered in services/mcp/tools.js and served from that registry: search-newsletters-tool,
search-articles-tool, search-entities-tool, get-newsletter-tool, get-newsletter-articles-tool,
get-newsletter-intel-tool, get-article-tool, get-sponsor-tool, list-topics-tool,
search-topics-tool, search-sponsors-tool, list-alerts-tool, list-watchlist-tool, get-topic-tool,
get-rising-newsletters-tool, get-entity-tool, create-export-tool. The endpoint answers 401 with a
WWW-Authenticate header and a pointer to auth.md when the key is missing, which is correct agent
behaviour.

Discovery files, all live: /.well-known/agent.json (protocol mcp, transport streamable-http, url
https://newsletterfit.com/mcp, bearer auth, scopes and actions), /auth.md, /llms.txt, /docs/api
(Scalar reference over the vendored bundle), /api/v1/openapi.yaml.

In app, session scoped and entitlement gated: watchlist (GET, PUT, DELETE, merge) and alerts
(list, create, patch, delete, deliveries, preferences, resolve). These are the monitoring half
of the product and they are not yet reachable with an API key.

Attribution: /api/click?lt=<token> mints tracked links for outreach, redirects server side and
adds UTM parameters at the redirect, so a click is attributable without a wrapped URL in the
email.

## The AI layer, and how much of it is exposed

Per article, the enrichment pass writes: summary, typed entities, sponsor placements with
evidence, topics, idealSponsors, audienceProfile, sentiment, keywords, sponsorThemes and guests.
The public API exposes only part of that: primaryTopic, the sponsor list and hasSponsor on the
article, the entity sections on the single article page, engagement, and the derived momentum and
sponsor aggregates on publications and sponsors. The internal fields (idealSponsors,
audienceProfile, sentiment, keywords) shape the aggregates that are served, but they are not
returned as raw fields.

Full issue text: Article.plainText exists in the database and is what the model reads. It is not
exposed by the REST API or by MCP, and the MCP tool description says so plainly ("Public metadata
only, no plainText or raw HTML"). A full text feed is therefore a capable but unshipped surface:
technically available from the store, legally a licensing question per publication, and it should
never be promised in a first touch as if it were an endpoint.

## Plans and limits, as configured

Free: 40 searches a month, no API. Pro at $49 a month: 2,000 searches, API access at 500 API
searches and 2,500 profile views, alerts and watchlists. Intelligence at $99: 10,000 searches,
2,000 API searches, 10,000 profile views, competitor tracking and custom monitoring, no exports.
Enterprise at $199: the same API ceilings plus 200 exports a month. In practice a data buyer that
needs volume is an Enterprise or a bespoke agreement, not a self serve plan, and saying that
early is more credible than quoting the plan grid.

## Verified limits, stated plainly

- No full text through the API or MCP. Derived records only, plus the article URL.
- Exports are Enterprise only and capped at a 14 day window. A longer history or a bulk backfill
  is a data agreement.
- Entities cannot be enumerated. GET /entities/:type without a q returns 400, so there is no way
  to page the whole entity graph today. Any "give me your entity list" request is a build.
- Entity typing carries noise. A probe of "brex" returned "Brexit" typed as technology, person
  and topic. Company and sponsor types look sound; the long tail needs a quality pass before it
  is sold as a clean graph.
- /usage returns 404 on this deployment, so a customer cannot self check metering. Metering
  itself works (UsageCounter increments on the metered routes).
- MCP has 17 tools, not the 38 sketched in the design. No webhook, report or alert write tool
  yet, and alerts and watchlist stay session scoped until a key scoped path exists.
- intel-summary is template generated. Do not present it as a model written brief.
- The sponsor directory totals are narrower than the corpus (951 sponsors, 1,895 placements, 625
  sponsored newsletters in the 365 day window on 2026-10-22). Useful internally, never a claim to
  put in copy.
