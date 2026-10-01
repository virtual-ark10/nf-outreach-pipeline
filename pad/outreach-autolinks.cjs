// outreach-autolinks.cjs — fill a draft's tracked links, and make every link an anchor.
//
// Two jobs, both about what the recipient SEES:
//
// 1. Fill the template. The first-email drafts are written from a template with literal
//    `[TRACKED_LINK]` on each recommended publication and `[Name]` in the signature, and
//    nothing filled them — a draft sat in the queue with no link (no attribution) and the
//    placeholder text itself would have gone to the prospect.
//
// 2. Never show a raw tracking URL. A visible `https://…/api/click?lt=<token>` reads as
//    spam and does not get clicked, so the html anchors the NAME: the publication on its
//    bullet, the brand on the signature. The plain-text part keeps names as names (text
//    has no anchors), which also keeps a token-laden URL out of a text-only reader's view.
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
// group 1 = the url, group 2 = the visible text (non-capturing inside, or the callback
// would receive the token where it expects the label)
const ANCHOR_RE = new RegExp(`<a\\s+[^>]*href=["'](${LT_URL_SRC})["'][^>]*>([\\s\\S]*?)<\\/a>`, 'g');

/** Unfilled template markers anywhere in the body — the send path refuses these. */
function unfilled(text, html) {
  const both = `${text || ''}\n${html || ''}`;
  const found = [];
  if (both.includes('[TRACKED_LINK]')) found.push('[TRACKED_LINK]');
  if (both.includes('[Name]')) found.push('[Name]');
  return found;
}

function norm(s) {
  return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

function esc(s) {
  return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

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

/** Corpus search -> best publication match, same scoring the internalize script uses. */
async function resolvePublication(name, cfg) {
  const sep = cfg.apiBase.endsWith('/') ? '' : '/';
  const payload = await httpJson(`${cfg.apiBase}${sep}search?q=${encodeURIComponent(name)}`, {
    headers: { Authorization: `Bearer ${cfg.token}` },
  });
  const results = (((payload || {}).data || {}).newsletters) || [];
  const q = norm(name);
  let best = null;
  for (const r of results) {
    const rName = r.name || '';
    const slug = r.slug || '';
    let score = 0;
    if (norm(rName) === q) score = 100;
    else if (norm(slug) === q) score = 95;
    else if (rName && q && (q.includes(norm(rName)) || norm(rName).includes(q))) score = 80;
    if (score && (!best || score > best.score)) best = { score, slug, name: rName };
  }
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

/** The publication named on a bullet: "- Tech Scoop — 155K subs …" -> "Tech Scoop". */
function bulletName(line) {
  return String(line).replace(/^\s*[-*•]\s*/, '').split(/\s+—\s+|\s+-\s+|\s+\|\s+|:/)[0].trim();
}

/** What a link's visible text should be: the publication on its bullet, else the brand. */
function anchorLabel(html, index) {
  const before = html.slice(0, Math.max(0, index));
  const segStart = Math.max(before.lastIndexOf('<p>'), before.lastIndexOf('<br>'), before.lastIndexOf('\n'));
  const segment = before.slice(segStart).replace(/<[^>]*>/g, ' ').trim();
  if (/Founder|NewsletterFIT\s*—?\s*$/i.test(segment) || !segment.includes('—')) return 'newsletterfit.com';
  return bulletName(segment.replace(/^[>\s]+/, '')) || 'newsletterfit.com';
}

/** Rewrite any anchor whose visible text is a raw tracking URL into its context label. */
function anchorifyHtml(html) {
  const src = String(html);
  return src.replace(ANCHOR_RE, (whole, url, visible, offset) => {
    if (!/newsletterfit\.com\/api\/click/.test(visible)) return whole;
    // The label comes out of the html, so it is already entity-encoded: strip anything
    // that could break the attribute, but do not re-escape its &amp; into &amp;amp;.
    const label = anchorLabel(src, offset).replace(/[<>"]/g, '').trim();
    return `<a href="${url}">${label}</a>`;
  });
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

  // ---- 2. signature: the brand link, anchored on the brand ------------------------
  const base = cfg.baseUrl.replace(/\/$/, '');
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
  for (const p of published) {
    const nameEsc = esc(p.name).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const inBlock = new RegExp(`(<p>(?:(?!</p>)[\\s\\S])*?)(${nameEsc})((?:(?!</p>)[\\s\\S])*?</p>)`);
    if (inBlock.test(html)) html = html.replace(inBlock, `$1<a href="${p.url}">$2</a>$3`);
    else html = html.replace(esc(p.name), `<a href="${p.url}">${esc(p.name)}</a>`);
  }
  if (signatureUrl) {
    const sigRe = /(Founder,\s*NewsletterFIT[^<]*—\s*)(?:<a[^>]*>)?newsletterfit\.com(?:<\/a>)?/;
    if (sigRe.test(html)) html = html.replace(sigRe, `$1<a href="${signatureUrl}">newsletterfit.com</a>`);
    else if (!TOKEN_IN_BODY_RE.test(html) && /newsletterfit\.com/.test(html)) {
      html = html.replace(/newsletterfit\.com/, `<a href="${signatureUrl}">newsletterfit.com</a>`);
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
};
