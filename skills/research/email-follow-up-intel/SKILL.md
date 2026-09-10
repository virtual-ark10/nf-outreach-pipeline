---
name: email-follow-up-intel
description: Prep prospect follow-up emails with verified corpus data.
version: 1.0.0
author: Nous Research
license: MIT
metadata:
  hermes:
    tags: [newsletterfit, sponsorship, outreach, followup, corpus, email]
    related_skills: [sponsor-outreach-pipeline, newsletterfit-corpus, newsletter-market-intelligence, last30days]
---

# Email Follow-Up Intel (NewsletterFIT)

## When to Use
- Preparing day-3 / day-7 / day-14 follow-up emails for NewsletterFIT prospects.
- Digging for a unique, verified data point / angle to make a follow-up land.
- Running the scheduled daily follow-up-intel scan on the lead sheet.

Prepares follow-up emails for NewsletterFIT prospects by mining the newsletter corpus,
last30days, and trusted web sources for UNIQUE, VERIFIED data about each prospect's product,
industry, brand, and competitors. The goal: give a lead one piece of data they cannot find
anywhere else, framed so it makes NewsletterFIT essential enough to book a call / sign up.

This skill is the CONTENT/INTEL layer of `sponsor-outreach-pipeline`. Load that skill for the
outreach cadence (Touch 2 day-3 bump, Touch 3 day-7 new evidence, Touch 4 day-14, stop after 4)
and the golden VERIFY rule. Combine with `newsletterfit-corpus` (corpus creds) and
`newsletter-market-intelligence` (query methods) and `last30days` (recent social/web signal).

## CORE POSITIONING — YOU SELL NEWSLETTER INTELLIGENCE FROM OUR CORPUS, NOT THE CLIENT'S OWN NEWS
This is the #1 rule for every pitch. The product we sell is "newsletter intelligence" — context
the client is blind to, derived from OUR data (what newsletters are writing, who sponsors whom,
whose topic-mentions are rising, which pubs are gaining momentum across 800K+ newsletters).
- NEVER lead with news about the client's own company. They already know their own product,
  launches, funding, and press. Restating it adds zero value and wastes the conversation.
- EVERY finding must tie back to our corpus. The value we provide is the gap between their world
  (which they know) and the newsletter-ecosystem view (which only we have). If a finding doesn't
  connect a newsletter-ecosystem fact to their context, it has no place in the email.
- Client's own company news is permitted ONLY as a bridge to corpus context (e.g. "your new Agent
  Stack targets AI builders — here are the newsletters those builders read and what is rising
  there"). The payoff is always what our data shows about their audience / pubs / competitors.
- PREVIOUS TOUCH RELEVANCY: only mention a pub we named in an earlier touch if a GENUINELY NEW, valuable corpus finding ties directly to the offer — and only then. Otherwise leave prior pubs out entirely. Do NOT include "X is still active" filler, do not re-list previously named pubs for the sake of recognition. Every sentence must be a wow-factor value-add.

## TIGHT + VALUE-DENSE COPY (length: keep the fuller proven drafts, don't over-compress)
- LENGTH: email length "has been great so far" — keep it there. Write a FULL value-led draft, roughly 4-6 sentences / ~130-170 words: an opening that ties to their product/industry, the unique verified finding with enough concrete detail to wow, a lineage or unifying read, and a low-friction "if useful" ask. Do NOT compress to 2-3 thin sentence stubs; the fuller version sustains the proof-first trust.
- Every line must still be a concrete, verified value-add (backed by corpus data + source). If a line adds no verified value, cut it — regardless of how nicely it reads. Wording/detail varies per prospect based on what the research found; there is no fixed template, so don't force filler to match a shape.
- One strongest corpus insight leads; support it with at most 2 corroborating points — don't pad.

## NO-SELL AT THIS STAGE — PROVIDE VALUE ONLY
- Day-3/7/14 follow-ups are VALUE DELIVERY, not selling. No pipeline talk, no rate cards, no
  "lock a slot", no hard CTA. CTA must be low-friction and helpful, mirroring the first emails:
  "Thought these might be useful for your next newsletter sponsorship search. If you're doing much
  newsletter buying, I can also show you what we're seeing across the broader market."  Or offer
  to send the articles / shortlist / reader profiles / momentum + booked sponsorships — "if useful".
- WE never book calls at this stage; we hand them value and let them come to us.

## PROSPECT COMPANY MUST BE VERIFIED AGAINST THE EMAIL DOMAIN FIRST
- Before any research, open the domain in the contact's email address and confirm what the company
  actually is. NEVER assume from the lead list. Real failures: kodiakls.com was KODIAK Construction
  Staffing (not a wealth/RIA firm); ollie.ai was a FAMILY AI assistant (not a code-review tool); and
  brand-spelling mixups (Migma vs Miga). A wrong persona is worse than no email — it destroys the
  thread. If the lead proves misdirected, flag it for re-assignment; do NOT force a pitch to the
  wrong company.

## FRESHNESS — NEVER SHIP STALE INTEL
- Pull the corpus queries and web sources fresh THIS RUN. Verify dates on every claim. Stale
  numbers or an old launch-page offered as new intel ruins credibility in one email. If the only
  signal you have is old, say "nothing new" rather than pass it off as current. — NEVER SHIP STALE INTEL
- Pull the corpus queries and web sources fresh THIS RUN. Verify dates on every claim. Stale
  numbers or an old launch-page as new intel ruins credibility in one email. If the only signal
  you have is old, say "nothing new" rather than pass it off as current.

## COMPETITOR CLAIMS — VERIFY THE RELATIONSHIP IS REAL BEFORE USING IT
- NEVER call a brand a lead's "direct competitor" from memory or a fuzzy category guess. A wrong competitor claim destroys trust. Real failure: Wispr Flow was drafted as Granola's "direct competitor" — but Wispr is a voice-DICTATION app, only ADJACENT to Granola's actual lane ("the AI notepad for back-to-back meetings", uses computer audio, no meeting bot). Verify the lead's real product (their own site) AND the would-be rival's real product before claiming overlap.
- Sweep the corpus for the TRUE direct competitors' sponsor placements before asserting any competitor buy. If zero confirmed placements, say so honestly and pivot to a defensible angle (e.g. open/uncontested sponsor lane while category coverage rises).
- When a draft cites "proof of discussion", anchor it to a REAL, dated article title from the corpus (pub + date + headline) and quote what it says. Never gesture at coverage without a concrete citation.
- Keep a competitor log per lead (see /home/boxed/lead_competitors.md). It is staged to be mirrored into the lead Google Sheet once OAuth write is wired.

## HARD RULE — NEVER INVENT DATA
- Do not fabricate numbers, subscribers, sponsor placements, trends, quotes, articles, or
  competitor claims.
- Only assert what you can point to: a real corpus doc id/URL, a real web source, or a real
  last30days output. Anchor every data point to a source you actually pulled this run.
- If nothing usable surfaces for a prospect, say so plainly — an honest "nothing yet" beats a
  fabricated datapoint. The first-touch already built trust on proof-first; never burn it.
- Corpus fields that are `ai.model=="mock"` / synthetic enrichment must NOT be cited as fact.

## TOPIC RELEVANCE — THE ENDS MUST MATCH THE PROSPECT'S ACTUAL LANE
- Every cited pub and every angle must be topically on-the-nose for the prospect's REAL product/industry, not just a generic "tech / AI / builder" reach fit. Real failure: a Ledger follow-up listed Marcus on AI, AI as Normal Technology and Simon Willison as "matching audiences" — but those are AI/model-builders, disconnected from Ledger's actual lane (crypto self-custody / hardware wallet / key security). High reach in the right-looking category does NOT mean relevant; a pub about the wrong-but-adjacent topic reads as off-target and burns the thread.
- To rebuild an on-topic angle: (1) restate from the verified domain what the prospect actually does (e.g. Ledger = crypto key security, not AI); (2) pull corpus pieces matching THAT lane's keywords (self-custody, hardware, secp256k1), not the broad category; (3) confirm whether any pub in that true lane even accepts/enrolls sponsors before pitching a "lane" — if none, say so and frame the early/under-contested angle honestly rather than naming generic pubs.
- Same test applies to the lead-insight article: if the fresh "proof of discussion" piece isn't in the prospect's stated lane, find one that is (e.g. a crypto-security piece for a hardware wallet), or drop it.

## Input
- Lead sheet: `/home/boxed/lead_tracking_sheet.csv` (columns: Company Name, Contact Emails,
  Title, Status, email body). Companions: `/home/boxed/sponsor_evidence.json`,
  `/home/boxed/sponsor_legit_class.json`, `hunter-contacts.csv`.
- Only follow up STATUSES in flight: First Email → Follow 2 → Follow 3 → Follow 4.
  Replied / Won / No = stop.
- Cadence per `sponsor-outreach-pipeline`: day 3 bump (FOLLOW-UP 2), day 7 new evidence
  (FOLLOW-UP 3), day 14 (FOLLOW-UP 4), stop after 4. Compute due = last send date + touch interval.

## Workflow per prospect

### 1. Profile the prospect — and VERIFY the domain first
OPEN the domain in the contact's email address (web_extract the homepage) and confirm the real
company + what they advertise / their audience before any pitch. Record: verified company name,
contact name/email/title, what they already sponsored (corpus or sheet), their industry/product.
One sentence of NewsletterFIT value for them. If the domain contradicts the lead (e.g. Kodiak
staffing vs wealth), STOP and flag for re-assignment — no pitch.

### 2. Corpus pass (NewsletterFIT) — THE primary layer, always the payoff
Query the newsletterFIT corpus (read-only Mongo + API): (a) their brand as mention/sponsor across
publications — GROWTH IN MENTIONS of their topic/category (rising topics, 7/30 days, momentum)
and WHO sponsors/looks like them; (b) competitor activity — which pubs competitors sponsor / are
mentioned, real placements only; (c) audience/idealSponsor fit with reach+momentum; (d) pubs they
ALREADY know about (from our prior touch or corpus) reinforced with a fresh personalized corpus
observation. Record exact claim + source (article/URL, publication, date). This corpus layer IS
the product we sell — every email must carry at least one corpus-derived insight the client is
blind to.

### 3. External signal (last30days) — secondary, optional
Run the last30days skill for the prospect's product/industry/competitors ONLY to find signal that
corroborates or extends a corpus finding. Capture real, recent, out-of-corpus signal. Record with
source. Degrade silently if nothing or source down — do not force it. Corpus-first: the web/30-day
signal supports, the corpus carries.

### 4. Deep research (web) — corroborate, never lead
Small web_search/extract spotchecks to CONTEXTUALIZE or VERIFY a corpus signal or a competitor
move — e.g. confirm a competitor's corpus presence by their real product. Record the URL. Corpus
facts are the moat; the web corroborates. Never present an unverified web claim as fact, and never
lead an email with plain web news about the client's own company.

### 5. Pick one strongest angle
Choose the single piece of genuinely-unique verified data. Rank: corpus-only evidence > verified
sponsor placements > industry trend w/ source > competitor activity w/ source > context. One wow
line + at most 2 corroborating lines. If nothing usable, report none.

### 6. Frame it for the email
Write: opening tying to their product/industry, the unique data with its source, a concrete
NewsletterFIT lens, and a low-friction ask (send the articles / shortlist / debrief). Never
include an unsourced number. Plain text, no markdown.

### 7. Update the lead sheet
Write the angle + framing to `/home/boxed/lead_tracking_sheet.csv` (the Follow N cell / a data
column) and advance Status to next touch, so the next run doesn't duplicate. Mirror to the
Google Sheet (google-workspace) when OAuth is up.

## Delivery (to the follow-up-intel channel)
Plain prose, no markdown symbols / no boxes. One block per prospect, under 1750 chars:

FOLLOW-UP — <Company> | <Contact> | <Touch N>
Data: <the unique verified finding>
Source: <how we know>
Email frame: full 4-6 sentence value draft (not compressed to 2-3 lines), opening + verified finding + ask

If no signal: "FOLLOW-UP — <Company> | <Contact> | Touch N: No new intel. Nothing usable in
the corpus or social this period — holding until a signal appears."

## Operating rules
1. Verify before asserting — every claim has a source. 2. Never invent. 3. One strong angle per
prospect. 4. Degrade gracefully. 5. Update the sheet so runs are idempotent. 6. Cite enough that
the operator can re-check. 7. Daily scan, one batch, then stop.

## Pitfalls
- `ai.model=="mock"` analysis is synthetic — never cite it as grounded.
- API field names ≠ Mongo raw field names — probe the schema.
- `.sh`-wrap anything that hits `corpus.env` to avoid the command-parser blocklist.
- Never read `/srv/newsletterfit` app secrets or write there; write under `/home/boxed`.
- NO Google Sheet WRITE on this box: READ of the link-shared lead sheet works via `export?format=csv` with no creds, but WRITE needs Google OAuth (no token/secret/gws/service-account exist; setup.py --check = NOT_AUTHENTICATED). Do not claim write access; stage sheet updates locally until one-time OAuth is done.