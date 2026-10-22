# Offer map: reading a prospect's gap, naming the NewsletterFIT surface that fills it

The work this replaces: a lane template that says "we have newsletter data, want a sample". It is
true and it is forgettable, because forty competitors can write the same sentence. The version
that lands ties three things together: a fact from the prospect's own documentation, the one
NewsletterFIT surface that answers it, and the honest limit of that surface.

## Step 1: read what the prospect tells you about its data appetite

The public documentation is a specification of what they will buy. Read four things.

1. Provider count. "160+ live providers", "100+ sources", "50+ data providers" is not a brag, it
   is an architecture: they resell other companies' records behind one interface. For them a new
   source is a connector, not a build, which is the cheapest sale in this market.
2. Enumerated categories. When the list of categories is closed (firmographics, funding, tech,
   stack, hiring, traffic, reviews, news) the gap is whatever the list omits. Name the omission in
   their own words, then stop.
3. Delivery surface already shipped. If they publish an MCP or an agent action library, lead with
   MCP. If they sell tables, lead with the export record shape. If they sell a signal stream, lead
   with the entity activity series.
4. Waterfall or bring your own key. A waterfall means they already sequence vendors and will
   sequence one more. A bring your own key model means a trial key is the whole proof.

Apollo through treg is the qualification pass on the company behind the docs, not the pitch. One
call gives headcount, revenue, funding stage, founding year, location and the technology list,
which is enough to decide whether to offer a self serve trial or a data agreement. Sample check
run 2026-10-22 on Diffbot: 31 employees, 8M revenue, Menlo Park, 48 technologies, keywords heavy
on knowledge graph, entity resolution and data enrichment. That profile says small, API first,
technically literate, so the offer leads with records and a trial key, not with a plan grid. Cost
0.026 dollars, one call.

**Funding is capped at 2026.** A round older than the current year is stale and cannot size an
offer: it says what a company raised years ago, not what it can spend now. Record the round with
its date, mark it stale, and qualify on headcount, revenue and technology instead. Diffbot's
Series A is from 2016, so it is recorded and ignored, which is also why the copy never mentions
funding.

## Step 2: the surfaces, and which gap each one answers

Use exactly one per touch. A first touch that lists four capabilities reads as a brochure.

Entity activity series (GET /entities/:type/:id): article and publication counts, first and
latest mention, mentions this month against the prior month, and the articles behind them. Answers
"we want newsletter discussion as a company signal" and "we want a time series, not a mention
count". Strongest for GTM signal platforms.

Sponsor graph (GET /directory/sponsors/:slug): placements with the evidence line from the issue,
a monthly placement series, the publications a brand runs in, similar sponsors by shared
inventory, and lookalike publications with audience and cadence. Answers "who is buying attention
in this category" and "which list should I buy next". Strongest for data marketplaces that want a
new category and for brands.

Publication object (GET /publications, /publications/by/:slug): audience label with its basis,
momentum with a window, cadence, topics, acceptsSponsors, detectedSponsoredPosts, recentSponsors,
content mix, monetization. Answers "there is no publication object in your enrichment library".
Strongest for enrichment and table products.

Article level attribution (GET /articles/:id): the sponsors detected in one issue, with evidence,
inside a graph of topics, people, companies and guests, plus engagement and related reading.
Answers "we can see the text but not who paid for it".

MCP (POST /mcp, 17 tools): agent callable reads across the same objects, typed ids, an export
tool. Answers "our agents need a tool, not a table". Strongest where the prospect already ships an
MCP.

Export snapshot (POST /exports, Enterprise): publications, articles, entities and links in one
call, and the publication record carries author contact fields with confidence and source.
Answers "we want to land this in our warehouse". Concede the 14 day window in the same breath.

Rising and momentum (GET /directory/rising): momentum computed under published rules from
repeated snapshots. Answers "how do you know it is rising". This is the one surface where the
method is the pitch, so state a rule rather than a percentage.

## Step 2b: when the gap cannot be proven, estimate it instead of stalling

A vendor with no public docs (Persana) cannot be shown to lack anything, and the earlier rule
held such a lead in "ask" form, which is honest but weak. The stronger move is an estimate built
from things the corpus does know: the company type, what the product does, and how the companies
it competes with in our own graph are covered.

Method, two calls per name: `GET /entities/company?q=<name>` to resolve the entity, then
`GET /entities/company/:id` for its page. Measured 2026-10-22:

    Clay        92 articles, 56 publications, 36 in the last 30 days
    Apollo     102 articles, 88 publications, 49 in the last 30 days
    ZoomInfo    25 articles, 19 publications, 13 in the last 30 days
    6sense      12 articles, 12 publications,  3 in the last 30 days
    Common Room  6 articles,  6 publications
    Lusha        5 articles,  4 publications
    Crustdata    4 articles,  3 publications
    Warmly       4 articles,  4 publications
    Unify        2 articles,  2 publications
    Persana      1 article,   1 publication
    Ocean.io, Bitscale, Databar, SyncGTM, Diffbot, Cognism: absent

So a lead that publishes no docs still gets a specific, checkable opener: their category is
already measured in the newsletter layer, their own name is not, and the numbers come from the
same graph the product sells. That is an estimate of need from comparables, not a claim about
what the prospect lacks, so the send gate accepts it and the copy does not have to ask permission
to make a point.

Two rules. Never inflate an absent company into a negative ("you are invisible"), which reads as
a taunt; state the comparison and let it land. And when the prospect is one of the measured names,
cite its own figure rather than the category's.

## Step 3: the ladder for a data or GTM buyer

Touch 1: their own enumeration, the omitted category, the one surface, the offer of a sample
shaped for their join. No counts, no percentages, no meeting request.
Touch 2: new information about that surface, ideally one verified example from the corpus, framed
as what the field looks like on their side.
Touch 3: the technical touch. Endpoint, docs URL, tool count, typed ids, and a trial key.
Touch 4: the commercial close. Sample export for a segment they pick, or a scoped data agreement.

## Step 4: per lead map (batch of 2026-10-22, all at stage leads, none contacted)

| Lead id | Published signal | Omitted category | Surface to lead with | Limit to concede |
| --- | --- | --- | --- | --- |
| bitscale | 15 intent signals, news sources named, 100+ sources, 18+ provider waterfall | no newsletter source in the signal list | entity activity series, then MCP | freshness follows ingest, not real time |
| databar | 160+ live providers, categories listed, contact and company fields | no publication object, no sponsor pair | publication object as a new column category, then export shape | exports are Enterprise, 14 day window |
| syncgtm | 50+ providers, 70+ fields, 75+ actions, own MCP action library | no publication or attribution object | publication object through their MCP actions | 17 tools today, no write tools beyond export |
| unify | 40+ sources, Upriver podcast sponsorships, PredictLeads news | newsletter sponsorship beside podcast sponsorship | sponsor graph and momentum method | placement history is what the corpus observed |
| warmly | eight source families in the MCP, normalised signal shape | no newsletter family | ninth source family, mapped to their fields | alerts stay in app for now |
| ocean-io | 16+ sources, filters and signals enumerated | no editorial coverage field | account field: publications that covered it | join is by domain, not by person |
| persana-ai | 75+ signals, 100+ intent sources | no public statement on newsletters | signal addition with provenance | gap unverified, so the copy asks |
| diffbot | KG crawled from the public web, "RSS feed for every press release, blog, newspaper or magazine", NL API | newsletters are not crawlable | entity and placement records with provenance | ontology mapping needed, full text by agreement |

## Copy rules that hold for every offer

- Subject: three to seven words, one idea, specific to the lead. Name what they get or the
  surface they would use it on. Never a cadence label, never two ideas joined by "and", never an
  abstraction ("provenance, and the text question" became "What one newsletter record contains").
- The first line is the second hook, so it is the pitch and never a description of the prospect's
  own product. They know what they sell. Open with the signal we derive, picked for their gap.
- The competitor comparison is that lead's own peer set, read from their docs and from the graph.
  The Apollo, Clay and ZoomInfo trio is right for CRM data vendors and wrong for a crawler
  (Bright Data, Apify) or a two to ten person startup (no comparison at all).
- No count of publications, articles or sponsors. Say corpus of newsletters, or newsletter
  intelligence. A count is both stale and easy to check.
- No em-dashes, no percentages, no "placement count" claims for a lead the corpus has never seen
  sponsor anything.
- Say what the product is in one sentence: AI reads the issues and returns extracted entities,
  sponsors and audience signals with provenance. It is not a database of ads.
- Name the limit before the prospect does. A conceded boundary buys the next paragraph.
- One ask, low friction: a sample shaped for their join, or a trial key. Never a meeting.
