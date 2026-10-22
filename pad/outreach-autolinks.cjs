// outreach-autolinks.cjs — fill a draft's tracked links, and make every link an anchor.
//
// Two jobs, both about what the recipient SEES:
//
// 1. Fill the template. The first-email drafts are written from a template with literal
//    `[TRACKED_LINK]` on each recommended publication and `[Name]` in the signature, and
//    nothing filled them — a draft sat in the queue with no link (no attribution) and the
//    placeholder text itself would have gone to the prospect.
//
// 2. Never show a raw tracking URL, and never repeat a name just to host one. A visible
//    `https://…/api/click?lt=<token>` reads as spam and does not get clicked, so the html
//    puts the link ON the first mention of the name — "- <a>Pub</a> — est. 169K", not
//    "- Pub — est. 169K: <a>Pub</a>" — and the brand anchor reads "NewsletterFIT" rather
//    than the bare domain. The plain-text part keeps names as names (text has no anchors),
//    which also keeps a token-laden URL out of a text-only reader's view.
//
// Conventions, matching what the outreach scripts already produce: dest is the
// publication's NewsletterFIT page (/app/publications/<slug>), ref is pub-<slug>-nf, the
// signature is the homepage (site-nf), and every minted token is mirrored into the local
// attribution store — the send path refuses a token it cannot resolve.
//
// Rules: idempotent (a draft that is already conforming is returned unchanged, so this can
// run on every save without double-minting); never blocks a save (an unresolved publication
// is reported and its placeholder left in place); the send path refuses a draft that still
// contains `[TRACKED_LINK]` or `[Name]`.
'use strict';

const { mintLink } = require('./outreach-links.cjs');

const LT_SRC = 'https:\\/\\/newsletterfit\\.com\\/api\\/click\\?lt=([A-Za-z0-9_-]{20,40})';
const LT_URL_SRC = 'https:\\/\\/newsletterfit\\.com\\/api\\/click\\?lt=[A-Za-z0-9_-]{20,40}';
const TOKEN_IN_BODY_RE = new RegExp(LT_SRC);
const BARE_TRACKED_RE = new RegExp(LT_SRC, 'g');
const ANY_TRACKED_RE = /api\/click\?lt=([A-Za-z0-9_-]{20,40})/g;
// Fixed pages worth attributing: a draft that names the docs or the MCP endpoint marks the spot
// with a placeholder, and prepareDraft mints a token for it. The plain-text part keeps a readable
// URL, the html carries the token, so a click on the docs is attributable without a wrapped link
// a reader has to trust.
const DOC_LINKS = {
  '[DOCS_LINK]': { path: '/docs/api', ref: 'docs-api-nf' },
  '[MCP_LINK]': { path: '/mcp', ref: 'mcp-nf' },
};
// group 1 = the url, group 2 = the visible text (non-capturing inside, or the callback
// would receive the token where it expects the label)
const ANCHOR_RE = new RegExp(`<a\\s+[^>]*href=["'](${LT_URL_SRC})["'][^>]*>([\\s\\S]*?)<\\/a>`, 'g');

/** Unfilled template markers anywhere in the body — the send path refuses these. */
function unfilled(text, html) {
  const both = `${text || ''}\n${html || ''}`;
  const found = [];
  if (both.includes('[TRACKED_LINK]')) found.push('[TRACKED_LINK]');
  if (both.includes('[Name]')) found.push('[Name]');
  for (const ph of Object.keys(DOC_LINKS)) {
    if (both.includes(ph)) found.push(ph);
  }
  return found;
}

function norm(s) {
  return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

function esc(s) {
  return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ---- resolution budget ---------------------------------------------------------------
// The sweep re-reads the whole queue every couple of minutes, and the corpus API allows 90
// requests/min for everything on this host. Re-resolving the same publication names every
// cycle emptied that bucket and left every bullet unresolved (2026-10-12: "0/90 filled,
// 0 link(s) minted" on every sweep, and 429s for every other corpus reader too). So a name
// is resolved once and remembered, a pass spends a bounded number of calls, and a 429 stops
// the pass instead of retrying into the limit. All three knobs are env-overridable.
const RESOLVE_TTL_MS = Math.max(0, parseInt(process.env.LINK_RESOLVE_TTL_MS || '21600000', 10) || 21600000);   // hit: 6h
const RESOLVE_MISS_TTL_MS = Math.max(0, parseInt(process.env.LINK_RESOLVE_MISS_TTL_MS || '3600000', 10) || 3600000); // miss: 1h
const MAX_RESOLVE_CALLS = Math.max(1, parseInt(process.env.LINK_RESOLVE_MAX_CALLS || '40', 10) || 40);
const RESOLVE_COOLDOWN_MS = Math.max(0, parseInt(process.env.LINK_RESOLVE_COOLDOWN_MS || '60000', 10) || 60000);
const resolveCache = new Map();   // norm(name) -> { at, best }
let resolveCalls = 0;             // reset per pass by the caller (server.cjs prepareQueue)
let rateLimitedUntil = 0;
// Kept out of the template literal: the authorization scheme word on its own line.
const BEARER_PREFIX = 'Bearer ';

function resetResolveBudget() { resolveCalls = 0; }
function isRateLimit(e) { return /HTTP 429\b/.test(String((e && e.message) || e)); }

function httpJson(urlStr, { method = 'GET', headers = {}, body = null, timeout = 20000 } = {}) {
  return new Promise((resolve, reject) => {
    const url = new URL(urlStr);
    const mod = url.protocol === 'https:' ? require('https') : require('http');
    const req = mod.request({
      hostname: url.hostname,
      port: url.port || (url.protocol === 'https:' ? 443 : 80),
      path: url.pathname + url.search,
      method,
      headers,
      timeout,
    }, (res) => {
      let data = '';
      res.on('data', (c) => { data += c; if (data.length > 2e6) req.destroy(); });
      res.on('end', () => {
        let parsed = null;
        try { parsed = JSON.parse(data); } catch { /* not json */ }
        if (res.statusCode < 200 || res.statusCode >= 300) {
          return reject(new Error(`HTTP ${res.statusCode}: ${data.slice(0, 160)}`));
        }
        resolve(parsed);
      });
    });
    req.on('error', reject);
    req.on('timeout', () => req.destroy(new Error('timeout')));
    if (body) req.write(body);
    req.end();
  });
}

/** Score corpus publication rows against a normalized name; keeps the best so far.
 *  Exact name (100) beats exact slug (95) beats a substring either way (80). */
function scorePublications(rows, q, best) {
  for (const r of (rows || [])) {
    const rName = r.name || '';
    const slug = r.slug || '';
    const rn = norm(rName);
    let score = 0;
    // A name written in a non-Latin script normalizes to the empty string, and an empty
    // needle is `includes`-ed by every query: it used to score 80 against any bullet and
    // take the mint, so 'This is For Real.' was linked to an unrelated Arabic-language
    // newsletter (2026-10-13). An empty normalized name can never be a match.
    if (!rn && !norm(slug)) continue;
    if (rn && q && rn === q) score = 100;
    else if (q && norm(slug) === q) score = 95;
    else if (rn && q && (q.includes(rn) || rn.includes(q))) score = 80;
    if (score && (!best || score > best.score)) best = { score, slug, name: rName };
  }
  return best;
}

/** Corpus search -> best publication match, same scoring the internalize script uses.
 *
 *  Several query shapes, because the search endpoint reads a short query as a topic and can
 *  bury the exact publication behind generic lists ("AI Agents Simplified" comes back behind
 *  'This Week in AI'). One query therefore resolves fewer names than exist, and a bullet that
 *  does not resolve keeps its [TRACKED_LINK] placeholder, which the send path refuses. */
async function resolvePublication(name, cfg) {
  const sep = cfg.apiBase.endsWith('/') ? '' : '/';
  const q = norm(name);
  const collapsed = String(name || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
  const head = String(name || '').split(/\s+/).slice(0, 2).join(' ');
  const queries = [...new Set([String(name || ''), collapsed, head].filter(Boolean))];
  // A name resolved (or found absent) once is not asked again until its entry ages out.
  const cached = resolveCache.get(q);
  if (cached && Date.now() - cached.at < (cached.best ? RESOLVE_TTL_MS : RESOLVE_MISS_TTL_MS)) return cached.best;
  if (Date.now() < rateLimitedUntil) return null;   // bucket empty: leave it for the next pass
  let best = null;
  let incomplete = false;
  for (const query of queries) {
    if (incomplete || resolveCalls >= MAX_RESOLVE_CALLS || Date.now() < rateLimitedUntil) { incomplete = true; break; }
    let payload = null;
    // A burst of bullets hits the search endpoint back to back; a single throttle or timeout
    // used to leave a bullet unminted for good, which blocks the draft at the send gate.
    for (let attempt = 0; attempt < 3 && !payload; attempt += 1) {
      resolveCalls += 1;
      try {
        payload = await httpJson(`${cfg.apiBase}${sep}search?q=${encodeURIComponent(query)}`, {
          headers: { Authorization: `${BEARER_PREFIX}${cfg.token}` },
          timeout: 8000,
        });
      } catch (e) {
        if (isRateLimit(e)) {                     // do not retry into a spent bucket
          rateLimitedUntil = Date.now() + RESOLVE_COOLDOWN_MS;
          incomplete = true;
          break;
        }
        if (attempt === 2) payload = null;
        else await new Promise((r) => setTimeout(r, 400 * (attempt + 1)));
      }
    }
    if (incomplete) break;
    best = scorePublications((((payload || {}).data || {}).newsletters) || [], q, best);
    if (best && best.score === 100) break;
  }
  // The search endpoint reads a short query as a topic and can bury or drop an exact
  // publication: it never returns 'The AI Corner', 'Interconnects AI', 'AI Futures Project',
  // 'Discover AI' or 'AI Action Letter' at all, though each is in the corpus with its own
  // subscriber label. A bullet naming one of them kept its [TRACKED_LINK] placeholder, which
  // the send path refuses, so five follow-ups sat at the gate. /publications?q= matches the
  // publication's own name and does return them (slug + label), so it is asked ONLY when the
  // search shapes found nothing — a name that already resolves is untouched. 2026-10-13.
  if (!incomplete && !best && resolveCalls < MAX_RESOLVE_CALLS && Date.now() >= rateLimitedUntil) {
    let payload = null;
    for (let attempt = 0; attempt < 3 && !payload; attempt += 1) {
      resolveCalls += 1;
      try {
        payload = await httpJson(
          `${cfg.apiBase}${sep}publications?q=${encodeURIComponent(String(name || ''))}&limit=10`,
          { headers: { Authorization: `${BEARER_PREFIX}${cfg.token}` }, timeout: 8000 },
        );
      } catch (e) {
        if (isRateLimit(e)) {                     // do not retry into a spent bucket
          rateLimitedUntil = Date.now() + RESOLVE_COOLDOWN_MS;
          incomplete = true;
          break;
        }
        if (attempt === 2) payload = null;
        else await new Promise((r) => setTimeout(r, 400 * (attempt + 1)));
      }
    }
    if (!incomplete) {
      const d = (payload || {}).data;
      best = scorePublications(Array.isArray(d) ? d : ((d && d.newsletters) || []), q, best);
    }
  }
  // Only a completed search is remembered: a pass cut short by the budget or a 429 must be
  // able to ask again next time (the cache is what makes the sweep converge, not a lockout).
  if (!incomplete) resolveCache.set(q, { at: Date.now(), best });
  return best;
}

/** "Ian Hinga <ian@newsletterfit.com>" -> "Ian Hinga" */
function displayName(from) {
  const m = String(from || '').match(/^\s*"?([^"<]+?)"?\s*</);
  return m ? m[1].trim() : '';
}

// The send path resolves every token against the local attribution mirror and REFUSES to
// send one it cannot find, so a token minted here has to be mirrored there.
function readStore(storePath) {
  const fs = require('fs');
  try {
    const d = JSON.parse(fs.readFileSync(storePath, 'utf8'));
    if (d && Array.isArray(d.clicks)) return d;
  } catch { /* missing or unreadable: start a fresh one */ }
  return { clicks: [], visits: [] };
}

function mirrorMinted(cfg, rows) {
  if (!rows.length || !cfg.storePath) return 0;
  const fs = require('fs');
  const store = readStore(cfg.storePath);
  const known = new Set(store.clicks.map((c) => c.token));
  let added = 0;
  for (const r of rows) {
    if (!r || !r.token || known.has(r.token)) continue;
    store.clicks.push({
      token: r.token,
      lead_id: r.lead_id || r.leadId || null,
      campaign: r.campaign || 'auto-links',
      link_ref: r.link_ref || r.ref || null,
      dest: r.dest || null,
      created_at: r.created_at || new Date().toISOString(),
      expires_at: r.expires_at || null,
      first_click_at: r.first_click_at || null,
    });
    known.add(r.token);
    added += 1;
  }
  if (added) fs.writeFileSync(cfg.storePath, JSON.stringify(store, null, 2) + '\n');
  return added;
}

/** Plain text -> simple html, so a text-only draft still carries real anchors. */
function textToHtml(text) {
  return String(text || '')
    .split(/\n{2,}/)
    .map((block) => `<p>${esc(block.trim()).replace(/\n/g, '<br>')}</p>`)
    .join('');
}

/** The publication named on a bullet: "- Tech Scoop — 155K subs …" -> "Tech Scoop".
 *  Handles the separator-free form the copy now uses, "- TheSequence (est. 169K)". */
function bulletName(line) {
  const first = String(line)
    .replace(/^\s*[-*•]\s*/, '')
    .split(/\s+—\s+|\s+-\s+|\s+\|\s+|:/)[0]
    .trim();
  return first.replace(/\s*\(([^)]*)\)\s*$/, '').trim();
}

/** Decode the entities esc() writes, so html text and labels can be compared as people read them. */
function decodeEntities(s) {
  return String(s || '')
    .replace(/&amp;/g, '&').replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'").replace(/&apos;/g, "'")
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>');
}

/** Visible text of an anchor's inner html, with the entities esc() writes decoded. */
function visibleText(inner) {
  return decodeEntities(String(inner || '').replace(/<[^>]*>/g, ' ')).replace(/\s+/g, ' ').trim();
}

/** [start, end) of every <a …>…</a> in s. */
function anchorSpans(s) {
  const spans = [];
  for (const m of String(s).matchAll(/<a\s+[^>]*>[\s\S]*?<\/a>/gi)) spans.push([m.index, m.index + m[0].length]);
  return spans;
}

/** How a decoded label can be written in html — a name with "&" or "'" is escaped in the source. */
function labelNeedles(label) {
  const variants = new Set([label]);
  if (label.includes('&')) {
    variants.add(label.replace(/&/g, '&amp;'));
    variants.add(label.replace(/&/g, '&#38;'));
  }
  if (label.includes("'")) variants.add(label.replace(/'/g, '&#39;'));
  if (label.includes('"')) variants.add(label.replace(/"/g, '&quot;'));
  return [...variants].filter(Boolean);
}

/**
 * The earliest occurrence of `label` in s that is NOT already inside an anchor.
 * @returns {{index: number, length: number}|null} — length is the matched spelling,
 *          so an escaped occurrence ("Greg &amp; Taylor") is replaced at its real width.
 */
function plainMatch(s, label) {
  const spans = anchorSpans(s);
  const hay = String(s).toLowerCase();
  let best = null;
  for (const needle of labelNeedles(label)) {
    const low = needle.toLowerCase();
    let from = 0;
    for (;;) {
      const i = hay.indexOf(low, from);
      if (i === -1) break;
      if (!spans.some(([a, b]) => i >= a && i < b)) {
        if (!best || i < best.index) best = { index: i, length: needle.length };
        break;
      }
      from = i + 1;
    }
  }
  return best;
}

/** Start of the line (bullet) the offset sits on — a link never migrates out of its bullet. */
function lineStartBefore(src, index) {
  return Math.max(
    src.lastIndexOf('<p>', index - 1),
    src.lastIndexOf('<br', index - 1),
    src.lastIndexOf('\n', index - 1),
  );
}

/** Apply offset-ordered edits (descending) so earlier offsets stay valid; skips overlaps. */
function applyEdits(src, edits) {
  let out = String(src);
  let floor = Infinity;
  for (const e of [...edits].sort((a, b) => b.from - a.from)) {
    if (e.to > floor) continue;
    out = out.slice(0, e.from) + e.text + out.slice(e.to);
    floor = e.from;
  }
  return out;
}

/** What a link's visible text should be: the publication on its bullet, else the brand. */
function anchorLabel(html, index) {
  const before = html.slice(0, Math.max(0, index));
  const segStart = Math.max(before.lastIndexOf('<p>'), before.lastIndexOf('<br'), before.lastIndexOf('\n'));
  const segment = decodeEntities(before.slice(segStart).replace(/<[^>]*>/g, ' ')).trim();
  if (/Founder|NewsletterFIT\s*—?\s*$/i.test(segment) || !segment.includes('—')) return 'NewsletterFIT';
  return bulletName(segment.replace(/^[>\s]+/, '')) || 'NewsletterFIT';
}

/**
 * Put every tracked link on the FIRST mention of the name it carries, and never repeat the
 * name to host it: "- <a>Pub</a> — est. 169K", not "- Pub — est. 169K: <a>Pub</a>". A link
 * whose visible text is still a raw tracking URL is relabelled (there is nothing to move to),
 * and the brand anchor reads "NewsletterFIT", not the bare domain.
 * Idempotent: once the link sits on the first mention there is no later copy left to move.
 */
function anchorifyHtml(html) {
  const src = String(html);
  const edits = [];
  for (const m of src.matchAll(new RegExp(ANCHOR_RE.source, 'g'))) {
    const [whole, url, inner] = m;
    const isRawUrl = /newsletterfit\.com\/api\/click/.test(inner);
    const shown = visibleText(inner);
    // A brand link written as its bare domain should name the brand, so the same
    // first-mention rule applies to it as to a publication.
    const label = (isRawUrl ? anchorLabel(src, m.index)
      : (/^newsletterfit\.com\/?$/i.test(shown) ? 'NewsletterFIT' : shown))
      .replace(/[<>"]/g, '').trim();
    if (!label) continue;

    const lineStart = lineStartBefore(src, m.index);
    const head = src.slice(lineStart + 1, m.index);
    const hit = plainMatch(head, label);
    if (!hit) {
      // Nothing earlier on this line to move the link to: name the anchor itself.
      if (isRawUrl) {
        edits.push({ from: m.index, to: m.index + whole.length, text: `<a href="${url}">${esc(label)}</a>` });
      }
      continue;
    }
    // The name is already written earlier on this line: the link moves there, the copy goes.
    const abs = lineStart + 1 + hit.index;
    edits.push({ from: abs, to: abs + hit.length, text: `<a href="${url}">${src.slice(abs, abs + hit.length)}</a>` });
    const tail = src.slice(abs + hit.length, m.index);
    const keep = /^([\s\S]*?)[\s]*(?:[:—–-]\s*)?$/.exec(tail);
    edits.push({ from: abs + hit.length + (keep ? keep[1].length : 0), to: m.index + whole.length, text: '' });
  }
  return applyEdits(src, edits);
}

/** Plain-text part: names stay names; no token-laden URL for a reader to see. */
function detokenText(text) {
  return String(text)
    .replace(new RegExp(`\\s*[—–-]\\s*${LT_SRC}`, 'g'), '')
    .replace(new RegExp(`:\\s*${LT_SRC}`, 'g'), '')
    .replace(new RegExp(`\\s*<${LT_SRC}>`, 'g'), '')
    .replace(BARE_TRACKED_RE, 'newsletterfit.com');
}

/**
 * Fill in and normalise a draft's links.
 * @returns { text, html, minted, unresolved, nameFilled, changed, mirrored, notes }
 */
async function prepareDraft(draft, cfg) {
  let text = String(draft.text || '');
  let html = String(draft.html || '');
  const campaign = draft.campaign || 'auto-links';
  const leadId = draft.leadId || draft.id || 'unknown';
  const minted = [];
  const unresolved = [];
  const notes = [];
  const published = [];            // { name, url } per filled bullet
  const rawMints = [];
  let changed = false;

  const textPlaceholders = (text.match(/\[TRACKED_LINK\]/g) || []).length;
  const htmlPlaceholders = (html.match(/\[TRACKED_LINK\]/g) || []).length;

  if ((textPlaceholders || htmlPlaceholders) && !cfg.token) {
    notes.push('no API_BEARER_TOKEN on this pad, so nothing could be minted');
    return { text, html, minted, unresolved, nameFilled: null, changed: false, mirrored: 0, notes };
  }

  // ---- 1. bullets: resolve, mint, and keep the NAME where the URL was --------------
  if (textPlaceholders) {
    const lines = text.split('\n');
    for (let i = 0; i < lines.length; i += 1) {
      if (!lines[i].includes('[TRACKED_LINK]')) continue;
      const raw = lines[i];
      const name = bulletName(raw);
      if (!name) { unresolved.push(raw.slice(0, 40).trim()); continue; }
      let resolved = null;
      try {
        resolved = await resolvePublication(name, cfg);
      } catch (e) {
        notes.push(`search failed for "${name}": ${e.message}`);
      }
      if (!resolved) { unresolved.push(name); continue; }
      try {
        const row = await mintLink(cfg, leadId, `${cfg.baseUrl.replace(/\/$/, '')}/app/publications/${resolved.slug}`, `pub-${resolved.slug}-nf`, campaign);
        rawMints.push(row);
        const url = `https://newsletterfit.com/api/click?lt=${row.token}`;
        lines[i] = raw.replace(/\s*[—–-]?\s*:?\s*\[TRACKED_LINK\]\s*$/, '').trimEnd();
        // Use the corpus's own name for the bullet, so the plain text and the anchor agree and a
        // subtitle the corpus does not carry cannot make the gate think the bullet is unlinked.
        if (resolved.name && norm(resolved.name) !== norm(name)) {
          const tail = (/\s*\(([^)]*)\)\s*$/.exec(raw) || [null, null])[1];
          lines[i] = `- ${resolved.name}${tail ? ` (${tail})` : ''}`;
        }
        published.push({ name: resolved.name || name, url });
        minted.push({ name: resolved.name || name, slug: resolved.slug, token: row.token, url });
        changed = true;
      } catch (e) {
        notes.push(`mint failed for "${name}": ${e.message}`);
        unresolved.push(name);
      }
    }
    text = lines.join('\n');
  }
  if (htmlPlaceholders) {
    const stripped = html.replace(/\s*[—–-]?\s*:?\s*\[TRACKED_LINK\]/g, '');
    if (stripped !== html) { html = stripped; changed = true; }
  }

  // ---- 1b. documentation links: a fixed page, minted so the click is attributable ---
  const base = cfg.baseUrl.replace(/\/$/, '');
  const docMinted = [];
  for (const [ph, spec] of Object.entries(DOC_LINKS)) {
    if (!text.includes(ph) && !html.includes(ph)) continue;
    const pretty = `newsletterfit.com${spec.path}`;
    if (!cfg.token) {
      unresolved.push(ph);
      notes.push(`no API_BEARER_TOKEN, so ${ph} could not be minted`);
      continue;
    }
    try {
      const row = await mintLink(cfg, leadId, `${base}${spec.path}`, spec.ref, campaign);
      const url = `https://newsletterfit.com/api/click?lt=${row.token}`;
      docMinted.push({ pretty, url });
      rawMints.push(row);   // the local mirror is the send gate's ledger: an unmirrored token
                            // cannot be resolved there and the draft is held
      text = text.split(ph).join(pretty);
      minted.push({ name: pretty, slug: null, token: row.token, url });
      changed = true;
    } catch (e) {
      notes.push(`mint failed for ${ph}: ${e.message}`);
      unresolved.push(ph);
    }
  }

  // ---- 1c. a bullet with no link is a claim the gate can only hold ------------------
  // A publication the resolver cannot place must not stay in the copy as an unlinked name. Try
  // once more (a label carrying a subtitle, "Linear: A Vertical Software ...", resolves on its
  // short name), and drop the bullet when it still cannot be placed, correcting the lead-in so
  // "Three lists" does not become a lie.
  {
    const linkedNames = new Set();
    for (const m of html.matchAll(/<a\s+[^>]*>([\s\S]*?)<\/a>/gi)) {
      linkedNames.add(norm(visibleText(m[1])));
    }
    for (const p of published) linkedNames.add(norm(p.name));
    const keep = [];
    let dropped = 0;
    for (const line of text.split('\n')) {
      if (!/^\s*[-*•]\s+/.test(line) || line.includes('[TRACKED_LINK]')) { keep.push(line); continue; }
      const name = bulletName(line);
      if (!name || linkedNames.has(norm(name))) { keep.push(line); continue; }
      let resolved = null;
      try { resolved = await resolvePublication(name, cfg); } catch (e) { resolved = null; }
      if (resolved && cfg.token) {
        try {
          const row = await mintLink(cfg, leadId, `${base}/app/publications/${resolved.slug}`,
            `pub-${resolved.slug}-nf`, campaign);
          rawMints.push(row);
          const url = `https://newsletterfit.com/api/click?lt=${row.token}`;
          published.push({ name: resolved.name || name, url });
          linkedNames.add(norm(resolved.name || name));
          minted.push({ name: resolved.name || name, slug: resolved.slug, token: row.token, url });
          // The bullet keeps the canonical name and drops a subtitle the corpus does not use:
          // "Linear: A Vertical Software & Vertical AI Newsletter (est. 10K)" -> "Linear (est. 10K)".
          // The gate matches the bullet to the anchor by name, so both sides must spell it the same.
          const tail = (/\s*\(([^)]*)\)\s*$/.exec(line) || [null, null])[1];
          const label = resolved.name || name;
          keep.push(`- ${label}${tail ? ` (${tail})` : ''}`);
          changed = true;
          continue;
        } catch (e) { notes.push(`mint failed for "${name}": ${e.message}`); }
      }
      dropped += 1;
    }
    if (dropped) {
      text = keep.join('\n');
      const n = keep.filter((l) => /^\s*[-*•]\s+/.test(l)).length;
      const word = { 1: 'One', 2: 'Two', 3: 'Three' }[n] || String(n);
      text = text.replace(/(One|Two|Three|\d+) ([a-z][\w\s-]*?) lists taking sponsors:/i,
        (_m, _w, lane) => `${word} ${lane} lists taking sponsors:`);
      notes.push(`dropped ${dropped} bullet(s) the corpus could not place`);
      changed = true;
    }
  }

  // ---- 2. signature: the brand link, anchored on the brand ------------------------
  const present = [...`${text}\n${html}`.matchAll(ANY_TRACKED_RE)].map((m) => m[1]);
  const byToken = new Map(readStore(cfg.storePath).clicks.map((c) => [c.token, c]));
  const homeRow = (() => {
    for (const t of present) {
      const row = byToken.get(t);
      if (row && String(row.dest || '').replace(/\/$/, '') === base) return t;
    }
    return null;
  })();
  let signatureUrl = homeRow ? `https://newsletterfit.com/api/click?lt=${homeRow}` : null;
  if (cfg.token && !signatureUrl && /newsletterfit\.com/.test(`${text}\n${html}`)) {
    try {
      const row = await mintLink(cfg, leadId, base, 'site-nf', campaign);
      rawMints.push(row);
      signatureUrl = `https://newsletterfit.com/api/click?lt=${row.token}`;
      minted.push({ name: 'NewsletterFIT (signature)', slug: null, token: row.token, url: signatureUrl });
      changed = true;
    } catch (e) {
      notes.push(`signature mint failed: ${e.message}`);
    }
  }

  // ---- 3. [Name] -> the sender -----------------------------------------------------
  let nameFilled = null;
  if (text.includes('[Name]') || html.includes('[Name]')) {
    const who = displayName(draft.from) || 'Ian Hinga';
    text = text.replace(/\[Name\]/g, who);
    html = html.replace(/\[Name\]/g, who);
    nameFilled = who;
    changed = true;
  }

  // ---- 4. text: no visible tracking URLs ------------------------------------------
  const detok = detokenText(text);
  if (detok !== text) { text = detok; changed = true; }

  // ---- 5. html: build when missing, anchor the names, clean raw-URL anchors --------
  if (!html.trim()) {
    html = textToHtml(text);
    changed = true;
  }

  // The docs and MCP anchors go in before the signature pass, so the brand-link fallback sees an
  // anchor already present and leaves the URL whole instead of cutting it into "NewsletterFIT/docs".
  for (const d of docMinted) {
    const at = html.indexOf(d.pretty);
    if (at !== -1) {
      html = html.slice(0, at) + `<a href="${d.url}">${d.pretty}</a>` + html.slice(at + d.pretty.length);
      changed = true;
    }
  }
  for (const p of published) {
    const nameEsc = esc(p.name).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const inBlock = new RegExp(`(<p>(?:(?!</p>)[\\s\\S])*?)(${nameEsc})((?:(?!</p>)[\\s\\S])*?</p>)`);
    if (inBlock.test(html)) html = html.replace(inBlock, `$1<a href="${p.url}">$2</a>$3`);
    else html = html.replace(esc(p.name), `<a href="${p.url}">${esc(p.name)}</a>`);
  }
  if (signatureUrl) {
    // The brand link sits on the FIRST mention of the brand, and the trailing domain goes
    // away: "Ian Hinga, Founder, <a …>NewsletterFIT</a>" — never "… NewsletterFIT — newsletterfit.com".
    const brandRe = /(Founder,\s*)(?:<a[^>]*>)?NewsletterFIT(?:<\/a>)?(\s*(?:[—–-]\s*)?(?:<a[^>]*>)?newsletterfit\.com(?:<\/a>)?)?/i;
    if (brandRe.test(html)) {
      html = html.replace(brandRe, (_whole, lead) => `${lead}<a href="${signatureUrl}">NewsletterFIT</a>`);
    } else if (!TOKEN_IN_BODY_RE.test(html) && !/<a\s/i.test(html) && /newsletterfit\.com/i.test(html)) {
      // Only a draft with no anchor at all gets its bare domain turned into the brand link: a
      // newsletterfit.com URL that is already a link (the docs page) must not be cut up.
      html = html.replace(/(?:<a[^>]*>)?newsletterfit\.com(?:<\/a>)?/i, `<a href="${signatureUrl}">NewsletterFIT</a>`);
    }
  }
  const beforeAnchors = html;
  html = anchorifyHtml(html);
  if (html !== beforeAnchors) changed = true;

  const mirrored = mirrorMinted(cfg, rawMints);
  return { text, html, minted, unresolved, nameFilled, changed, mirrored, notes };
}

module.exports = {
  prepareDraft, resolvePublication, unfilled, displayName,
  anchorifyHtml, detokenText, bulletName, textToHtml, TOKEN_IN_BODY_RE,
  resetResolveBudget,
};
