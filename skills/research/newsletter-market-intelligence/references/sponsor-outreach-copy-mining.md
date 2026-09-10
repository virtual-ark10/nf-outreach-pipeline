# Sponsor outreach: mining REAL ad copy for cold-email personalization

Recipe from `newsletterfit` sponsor-outreach (solved 2026-08-26). The corpus's
highest-value outreach asset is the sponsors' **actual published ad copy** — quote
it in the first line of a cold email and you stop looking like a mass blast.

## Where the copy lives

Flow: `articlementions (type=sponsor)` → `articleId` → `articleraws.body_html`.

- `articlementions` rows with `type=="sponsor"` carry `{articleId, entity, publicationId,
  confidence>0.9}`. `entity` is the sponsor name.
- `articleraws.body_html` is the stored HTML for the host article; the sponsor ad is
  embedded inside it (e.g. "This newsletter is sponsored by Tracksuit, the always-on brand
  tracker built for marketers and agencies..."). Payloads **expire** (early Sept in this
  corpus), so mine while fresh.
- Confirmed available for: Tracksuit, Brex, Granola, LMNT, HubSpot, Incogni, Render,
  MongoDB, Ahrefs, Attio, Notion. **Missing for: Glean, DeleteMe** (no body_html row) —
  fall back to placement + lookalike layers (Layer 2/3), never fake a quote.

## Database query skeleton (mongosh)

Run from a `.sh` that sources `corpus.env` (see newsletter-market-intelligence SKILL).
Bulk over a name list:

```javascript
const names = ["Tracksuit","Brex","Granola","LMNT","HubSpot","Incogni","Render","MongoDB","Ahrefs","Attio","Notion"];
const esc = {Tracksuit:"Tracksuit",/* ...re.escape each */};
const out = {};
(function(){                                    // MUST wrap in IIFE
  for (const n of names){
    const rx = new RegExp("^"+esc[n]+"$","i");
    const ms = db.articlementions.find({"type":"sponsor","entity":rx},{articleId:1}).limit(2).toArray();
    const ids = ms.map(m=>m.articleId);
    const d = db.articleraws.findOne({articleId:{$in:ids}, "payload.body_html":{$exists:true}});
    if(!d){ out[n]="NO_COPY"; continue; }        // e.g. Glean/DeleteMe
    const text = (d.payload.body_html||"").replace(/<[^>]+>/g," ").replace(/\s+/g," ").trim();
    const i = text.toLowerCase().indexOf(n.toLowerCase());
    if(i<0){ out[n]="MATCH_NOT_IN_BODY"; continue; }
    out[n] = (i<150) ? text.substring(i,i+550) : text.substring(Math.max(0,i-60), i+480);
  }
})();
print(JSON.stringify(out));
```

## mongosh gotchas (cost real time)

- **No top-level `return`** in `mongosh --eval` — it's an expression context; any
  `function(){ return ... }()` at top level throws `SyntaxError: 'return' outside of
  function`. Wrap the whole body in an IIFE.
- **`tojson()` is undefined** in `mongosh --quiet`; use `print(JSON.stringify(...))`.
- Strip HTML *before* computing the brand-match index (the byte offset is meaningless on
  raw HTML); then `substring` around the found index. Verify `body_html` length != 0; some
  rows have `plaintext:""` / no copy.

## Personalization layers (conversion rank)

1. **Layer 1 — quote their real ad copy** (strongest, rarest):
   "Found the Brex placement in Sourcery — 'cards, expenses, travel, bill pay, banking
   wrapped into a high-performance stack.' That's a founder-infra message..."
2. **Layer 2 — cite exact placement**: publication, article, category, subscriber count;
   bridge via their observed topics.
3. **Layer 3 — one matched lookalike with a reason**, not a list: top similarity + one
   momentum datapoint (e.g. "+42% Supercompanies").
4. **CTA by tier**: repeat buyers → book a call; fresh single placement → soft momentum
   share; stale (>30d) → one light email; enterprise (AWS/Salesforce/Google) → ABM,
   skip template.

## Test for "is this copy real"

Check whether host pub is active and the sponsor mention is a placement, not editorial:
`db.articlementions.find({"type":"sponsor","entity":/^Tracksuit$/i},{confidence:1})` —
confidence ≥ ~0.95. Never quote copy for a sponsor that returned NO_COPY/MATCH_NOT_IN_BODY.