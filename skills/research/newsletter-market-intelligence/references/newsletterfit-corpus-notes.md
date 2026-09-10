# NewsletterFIT corpus — field-survey notes (from a real session)

Recovered from exploring the local corpus. Credentials for the API/Mongo live in
`~/.config/newsletterfit/corpus.env` (the `newsletterfit-corpus` skill holds access rules).

## Confirmed collection counts (2026-08-23)
- articleraws: 10,323   (stored HTML/text payloads, ~68KB each, key `articleId`)
- articles: 10,549
- publications: 1,522
- articlementions: 59,268
- entities: 38,526
- articlesnapshots: 1,109 | publicationsnapshots: 3,015 | qualityissues: 122 | searchcaches: 1

## API vs raw-Mongo field duality (the big schema trap)
- `GET $NEWSLETTERFIT_API/publications?limit=N` returns **normalized** fields:
  `id, slug, name, category, subscribers, subscribersLabel, sponsorMentions, momentumPct,
  momentumLabel, rising, reason`. Clean for lookups.
- `GET $NEWSLETTERFIT_API/search?q=...&limit=N` returns:
  `newsletters[]` (same shape) + `topics[] {name,count}` + `sponsors[]` + `companies[]`
  + `mentions[] {id,title,publicationName,when,published}`.
- Raw Mongo `publications` docs use `publicationId, name, subdomain, customDomain, baseUrl,
  author, subscriberEstimate, subscriberDisplay, tier, hasPodcast, categories, topics[],
  entities[], profile, stats, sponsorship, idealSponsors[], capability, ...`. Analytics are nested:
  - `stats.=`.{sponsorFit, reachTier, momentumScore, importScore, dataConfidence, articleCount,
    avgReactions, avgComments, avgRestacks, avgWords, publishingFrequency, lastPost}`
  - `sponsorship.{acceptsSponsors, contactEmail, mediaKit, price, sponsorFormats[],
    recentSponsors[], detectedSponsoredPosts, lastChecked}`
  - `idealSponsors[]` (objects with `category`, sometimes `confidence`)
- Raw Mongo `articles` docs use `articleId, publicationId, publicationName, title, slug, url,
  published, audience, wordCount, comments, childComments, restacks, reactions[], section, tags,
  plainText, content, analysis, ai, enrichment`. Commentary:
  - `reactions` = ARRAY of `{emoji, count}` (sum counts to rank — sorting the array → [object Object])
  - `analysis.{readingTime, complexity, primaryTopic, secondaryTopics[], companyMentions[],
    productMentions[], hasSponsor, sponsorConfidence}`
  - `ai.{version, enrichedAt, model, summary, entities[], topics[], idealSponsors[],
    audienceProfile[], sentiment, keywords[], sponsorThemes[]}`
  - `enrichment.{status, version, attempts, completed}`

## The confirmed gap (the business thesis)
`ai.model == "mock"` on sampled articles — enrichment is synthetic/placeholder. Concretely,
ByteByT-ByteGo's "Hiring: Part-Time Instructor" post (a hiring/jobs sponsorship) was labeled
`analysis.hasSponsor=false, sponsorConfidence=0, ai.sponsorThemes=[]`. Publication-level sponsor
graph is also unaggregated: `detectedSponsoredPosts: 0`, `acceptsSponsors: null`,
`recentSponsors: []` even on Diamond/Platinum pubs whose raw payloads contain sponsor copy.
So: corpus is wide, monetization layer is a mannequin → the unbuilt sponsor-intelligence /
lead-gen layer is where founder ideas came from (rating index, sponsor-reputation detector,
rising-topic sell signals, founder-concierge lead mapping, rate-card standard).

## Grounded aggregates captured (use to sanity-check, don't re-derive blindly)
- AudienceProfile frequencies (of ~4000 enriched articles): policy makers 309, product managers
  235, founders 147, journalists 145, AI researchers 129, ML engineers 99, investors 99, ...
- IdealSponsor categories: Cloud infrastructure 342, B2B SaaS 214, Developer tools 209, AI APIs
  206, Productivity software 181, Political consulting 152, Cybersecurity 116, Financial services
  100, ...
- sponsorThemes: political analysis 38, developer productivity 36, legal compliance 35, AI infra
  21, AI ethics 19, ...
- Top reach: Lenny's ~1.2M, Pragmatic Engineer ~1.1M, Parnas Perspective ~890K (tiers Diamond /
  Platinum).
- Topic taxonomy across pubs is hyper-split on AI (AI / AI agents / AI infrastructure / AI
  governance / AI safety / AI model / ChatGPT / Claude) — useful signal that "AI" is fragmented
  into many commercially distinct communities.