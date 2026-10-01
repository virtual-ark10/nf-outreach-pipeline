// outreach-autolinks.cjs — fill a draft's tracked links before it can ever be sent.
//
// Why this exists: the first-email drafts are generated as templates with literal
// placeholders — "[TRACKED_LINK]" on each recommended publication and "[Name]" in the
// signature — and nothing ever filled them. So a draft sat in the queue with no minted
// link at all (no attribution possible) and, worse, would have sent the placeholder
// text itself to the recipient.
//
// This pass closes that: for each bullet naming a publication, it resolves the
// publication to its NewsletterFIT page via the corpus search, mints a token for that
// page through POST {apiBase}/outreach/links, and puts the tracked URL where the
// placeholder was. Same conventions the outreach scripts already use: dest is the
// internal page, ref is pub-<slug>-nf, and the signature link is the homepage (site-nf).
//
// Rules:
//   - Idempotent. A draft that already carries a token, with no placeholder left, is
//     returned untouched, so this can run on every save without ever double-minting.
//   - Never blocks a save. An unresolved publication is reported and its placeholder is
//     left in place; the send path refuses to send a draft that still has one.
//   - Never invents a link for a draft that has no placeholder: such a draft gets the
//     signature link only, so every outgoing mail is attributable at least at the site.
'use strict';

const { mintLink, linkifyHtml } = require('./outreach-links.cjs');

const TOKEN_IN_BODY_RE = /https:\/\/newsletterfit\.com\/api\/click\?lt=([A-Za-z0-9_-]{20,40})/;
const BARE_SITE_RE = /newsletterfit\.com(?!\S*api\/click)/g;

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

// The send path resolves every token against the local attribution mirror and REFUSES
// to send one it cannot find, so a token minted here has to be mirrored there or the
// draft becomes unsendable. Same file, same shape the outreach scripts write.
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
  const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  return String(text || '')
    .split(/\n{2,}/)
    .map((block) => `<p>${esc(block.trim()).replace(/\n/g, '<br>')}</p>`)
    .join('');
}

/** Replace the bare signature mention (the LAST one) with the tracked url. */
function trackSignature(text, url) {
  const hits = [...text.matchAll(BARE_SITE_RE)];
  if (!hits.length) return `${text.trimEnd()}\n\nIan Hinga, Founder, NewsletterFIT — ${url}\n`;
  const last = hits[hits.length - 1];
  return text.slice(0, last.index) + url + text.slice(last.index + last[0].length);
}

/**
 * Fill in a draft's links.
 * @returns { text, html, minted: [{name, slug, token, url}], unresolved: [name],
 *            nameFilled: string|null, changed: boolean, notes: [string] }
 */
async function prepareDraft(draft, cfg) {
  let text = String(draft.text || '');
  let html = String(draft.html || '');
  const campaign = draft.campaign || 'auto-links';
  const leadId = draft.leadId || draft.id || 'unknown';
  const minted = [];
  const unresolved = [];
  const notes = [];
  let changed = false;

  const textPlaceholders = (text.match(/\[TRACKED_LINK\]/g) || []).length;
  const htmlPlaceholders = (html.match(/\[TRACKED_LINK\]/g) || []).length;

  if ((textPlaceholders || htmlPlaceholders) && !cfg.token) {
    notes.push('no API_BEARER_TOKEN on this pad, so nothing could be minted');
    return { text, html, minted, unresolved, nameFilled: null, changed: false, notes };
  }

  const urlsInOrder = [];
  const rawMints = [];

  // One publication per line that carries a placeholder. The line reads
  // "- Tech Scoop — 155K subs, AI/agents — [TRACKED_LINK]", so the name is what
  // precedes the first separator.
  if (textPlaceholders) {
    const lines = text.split('\n');
    for (let i = 0; i < lines.length; i += 1) {
      if (!lines[i].includes('[TRACKED_LINK]')) continue;
      const raw = lines[i];
      const namePart = raw.replace(/^\s*[-*•]\s*/, '').split(/\s+—\s+|\s+-\s+|\s+\|\s+|:/)[0].trim();
      if (!namePart) { unresolved.push(raw.slice(0, 40).trim()); continue; }
      let resolved = null;
      try {
        resolved = await resolvePublication(namePart, cfg);
      } catch (e) {
        notes.push(`search failed for "${namePart}": ${e.message}`);
      }
      if (!resolved) { unresolved.push(namePart); continue; }
      let token = null;
      try {
        const row = await mintLink(cfg, leadId, `${cfg.baseUrl.replace(/\/$/, '')}/app/publications/${resolved.slug}`, `pub-${resolved.slug}-nf`, campaign);
        token = row.token;
        rawMints.push(row);
      } catch (e) {
        notes.push(`mint failed for "${namePart}": ${e.message}`);
        unresolved.push(namePart);
        continue;
      }
      const url = `https://newsletterfit.com/api/click?lt=${token}`;
      lines[i] = raw.replaceAll('[TRACKED_LINK]', url);
      urlsInOrder.push(url);
      minted.push({ name: resolved.name || namePart, slug: resolved.slug, token, url });
      changed = true;
    }
    text = lines.join('\n');
  }

  // The same bullets in an existing html body, in the same order.
  if (htmlPlaceholders) {
    let n = 0;
    html = html.replace(/\[TRACKED_LINK\]/g, () => (n < urlsInOrder.length ? urlsInOrder[n++] : '[TRACKED_LINK]'));
    if (n) changed = true;
  }

  // The signature: every draft should carry the site link, attributed to this lead —
  // the older drafts carry it alongside their publication links. Decided from the
  // attribution store rather than from the text, because an anchor whose *text* is
  // "newsletterfit.com" is already tracked and must not be minted again.
  const base = cfg.baseUrl.replace(/\/$/, '');
  const present = [...`${text}\n${html}`.matchAll(/api\/click\?lt=([A-Za-z0-9_-]{20,40})/g)].map((m) => m[1]);
  const byToken = new Map(readStore(cfg.storePath).clicks.map((c) => [c.token, c]));
  const hasSignature = present.some((t) => {
    const row = byToken.get(t);
    return row && String(row.dest || '').replace(/\/$/, '') === base;
  });
  if (cfg.token && !hasSignature && /newsletterfit\.com/.test(`${text}\n${html}`)) {
    try {
      const row = await mintLink(cfg, leadId, base, 'site-nf', campaign);
      rawMints.push(row);
      const url = `https://newsletterfit.com/api/click?lt=${row.token}`;
      text = trackSignature(text, url);
      if (html.trim()) html = trackSignature(html, url);
      minted.push({ name: 'NewsletterFIT (signature)', slug: null, token: row.token, url });
      changed = true;
    } catch (e) {
      notes.push(`signature mint failed: ${e.message}`);
    }
  }

  // [Name] -> the sender's own name, taken from the draft's From header.
  let nameFilled = null;
  if (text.includes('[Name]') || html.includes('[Name]')) {
    const who = displayName(draft.from) || 'Ian Hinga';
    text = text.replace(/\[Name\]/g, who);
    html = html.replace(/\[Name\]/g, who);
    nameFilled = who;
    changed = true;
  }

  // Anchors: build html from the text when there is none, then wrap bare URLs so the
  // tracked link is clickable in both parts. Any placeholder still left stays visible
  // on purpose — the send path refuses it, and masking it here would hide the problem.
  if (changed) {
    html = linkifyHtml(html.trim() ? html : textToHtml(text));
  }

  const mirrored = mirrorMinted(cfg, rawMints);

  return { text, html, minted, unresolved, nameFilled, changed, mirrored, notes };
}

module.exports = { prepareDraft, resolvePublication, unfilled, textToHtml, displayName, TOKEN_IN_BODY_RE };
