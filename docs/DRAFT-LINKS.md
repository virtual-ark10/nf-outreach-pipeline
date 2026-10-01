# Draft links: how a draft's tracking links get in, and how they look

Written by the pad, not by hand. `pad/outreach-autolinks.cjs` runs:

- on save over the API (`PUT /api/drafts/:id`),
- before every send,
- and on a sweep every two minutes (`DRAFT_LINK_SWEEP_MS`), which is what catches drafts
  written straight into the SQLite store by a seeder or a generator.

It does three things:

1. **Fills the template.** A bullet naming a publication — `<Pub> — est. <subs>: [TRACKED_LINK]`
   — is resolved through the corpus search to that publication's NewsletterFIT page, a token
   is minted for it, and the placeholder goes away. `[Name]` becomes the sender from the
   draft's From header. The signature gets the homepage token (`site-nf`).
2. **Anchors the names, once.** In html the link sits on the FIRST mention of the name — the
   publication at the head of its bullet, the brand on the signature — so a bullet reads
   `- <a href="…">TheSequence</a> — est. 169K` and never repeats the name to host the link
   (`- TheSequence — est. 169K: <a href="…">TheSequence</a>` is the bug this replaced). The
   brand anchor reads `NewsletterFIT`, not the bare domain. A raw `…/api/click?lt=<token>` is
   never the visible text of a link: it reads as spam and does not get clicked. The
   plain-text part carries no token-laden URLs at all — names stay names.
3. **Mirrors the tokens** into the attribution store (`/home/boxed/newsletterfit/attribution/attribution.json`),
   because the send path refuses a token it cannot resolve.

`POST /api/drafts/prepare-links` (pad token) runs the whole queue on demand and reports
what it minted and what it could not resolve. A draft that still contains `[TRACKED_LINK]`
or `[Name]` is **refused at send** with the placeholder named, so an unfilled template can
never reach a prospect.

Staging note: this pad's store is chosen by `OUTREACH_DB`, **not** `DATA_DIR` — point a test
instance at a copy of the database or it writes the live CRM.
