// outreach-links.cjs — send-time link internalization for the Resend Pad.
//
// Guarantees (fail-closed — an email NEVER goes out broken or leaking traffic):
//   1. Every tracked link (newsletterfit.com/api/click?lt=...) uses a
//      SERVER-KNOWN token minted via POST /api/v1/outreach/links. Old
//      locally-minted 32-hex tokens are dead server-side (/api/click 404s)
//      and are re-minted automatically at send time. The local attribution
//      store doubles as the dedupe mirror.
//   2. No external (non-newsletterfit.com) http(s) link leaves the pad,
//      tracked or not — all outreach links stay internal (outreach policy).
//
// If anything is unresolvable (unknown token, external dest, API down) the
// send is BLOCKED with a human-readable message — never silently sent.
//
// Config (cfg):
//   apiBase   — newsletterfit API base, e.g. http://127.0.0.1:3000/api/v1
//   token     — API bearer token ('' disables re-minting, still enforces policy)
//   storePath — local attribution store mirror (attribution.json)
//   baseUrl   — https://newsletterfit.com (internal dest prefix)
'use strict';

const fs = require('fs');
const http = require('http');
const https = require('https');

const LT_RE = /https:\/\/newsletterfit\.com\/api\/click\?lt=([A-Za-z0-9_-]{20,40})/g;
const URL_RE = /https?:\/\/[^\s"'<>)]+/g;
const OLD_LOCAL_RE = /^[a-f0-9]{32}$/;

class InternalizeError extends Error {}

function isApiToken(tok) {
  // Server-minted tokens are base64url; old local mints were 32-lowercase-hex.
  return !OLD_LOCAL_RE.test(tok) && /^[A-Za-z0-9_-]{22,32}$/.test(tok);
}

function short(tok) { return `lt=${tok.slice(0, 10)}…`; }

function readStore(storePath) {
  try {
    const d = JSON.parse(fs.readFileSync(storePath, 'utf8'));
    return (d && Array.isArray(d.clicks)) ? d : { clicks: [], visits: [] };
  } catch {
    return { clicks: [], visits: [] };
  }
}

// POST {NEWSLETTERFIT_API}/outreach/links -> resolves with the minted row.
function mintLink(cfg, leadId, dest, ref, campaign) {
  return new Promise((resolve, reject) => {
    const url = new URL(cfg.apiBase.replace(/\/+$/, '') + '/outreach/links');
    const body = JSON.stringify(campaign
      ? { leadId, dest, ref, campaign }
      : { leadId, dest, ref });
    const mod = url.protocol === 'https:' ? https : http;
    const req = mod.request({
      hostname: url.hostname,
      port: url.port || (url.protocol === 'https:' ? 443 : 80),
      path: url.pathname,
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${cfg.token}`,
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(body),
      },
      timeout: 20000,
    }, (res) => {
      let data = '';
      res.on('data', (c) => { data += c; if (data.length > 1e6) req.destroy(); });
      res.on('end', () => {
        let payload;
        try { payload = JSON.parse(data); } catch { payload = null; }
        if (res.statusCode < 200 || res.statusCode >= 300) {
          const msg = (payload && payload.error && payload.error.message) ? payload.error.message : data.slice(0, 200);
          return reject(new InternalizeError(`mint failed (HTTP ${res.statusCode}): ${msg}`));
        }
        const d = (payload && payload.data) || {};
        if (!d.token) return reject(new InternalizeError('mint response missing token'));
        resolve(d);
      });
    });
    req.on('error', (e) => reject(new InternalizeError(`mint network error: ${e.message}`)));
    req.write(body);
    req.end();
  });
}

// Core: rewrite tracked links to server-known internal tokens, fail on externals.
// Returns { text, html, minted } with text/html rewritten. Throws InternalizeError.
async function internalize(rawText, rawHtml, leadId, cfg) {
  const text = rawText || '';
  const html = rawHtml || '';
  const store = readStore(cfg.storePath);
  const byToken = new Map(store.clicks.map((c) => [c.token, c]));

  const tokens = new Set();
  for (const m of `${text}\n${html}`.matchAll(LT_RE)) tokens.add(m[1]);

  let newText = text;
  let newHtml = html;
  const minted = [];
  let dirty = false;

  for (const tok of tokens) {
    const row = byToken.get(tok);
    if (!row) {
      throw new InternalizeError(`${short(tok)} is not in the attribution store — run outreach_internalize.py or mint it via the API before sending`);
    }
    const dest = row.dest || '';
    if (!dest.startsWith(cfg.baseUrl)) {
      throw new InternalizeError(`${short(tok)} still points externally (${dest}) — run outreach_internalize.py to internalize it before sending`);
    }
    if (isApiToken(tok)) continue; // already server-known and internal

    if (!cfg.token) {
      throw new InternalizeError(`cannot re-mint ${short(tok)}: pad has no API_BEARER_TOKEN — set it in corpus.env / .env`);
    }
    const slug = dest.split('/').pop();
    const nt = await mintLink(cfg, leadId, dest, `pub-${slug}-nf`, row.campaign || 'pad-send');
    byToken.set(nt.token, nt);
    store.clicks.push(nt);
    minted.push(nt);
    dirty = true;
    const oldLink = `https://newsletterfit.com/api/click?lt=${tok}`;
    newText = newText.replaceAll(oldLink, `https://newsletterfit.com/api/click?lt=${nt.token}`);
    newHtml = newHtml.replaceAll(oldLink, `https://newsletterfit.com/api/click?lt=${nt.token}`);
  }

  // Fail closed on ANY external link — all outreach links stay on newsletterfit.com.
  for (const [part, s] of [['text', newText], ['html', newHtml]]) {
    for (const m of s.matchAll(URL_RE)) {
      let host;
      try { host = new URL(m[0]).host; } catch { continue; }
      if (host !== 'newsletterfit.com') {
        throw new InternalizeError(`outgoing email links out to ${host} (${m[0].slice(0, 90)}) — all outreach links must stay on newsletterfit.com (internalize it before sending)`);
      }
    }
  }

  if (dirty) {
    fs.writeFileSync(cfg.storePath, JSON.stringify(store, null, 2) + '\n');
  }
  return { text: newText, html: newHtml, minted };
}

// Wrap bare http(s) URLs in <a href="...">...</a> anchors for the HTML body.
// Idempotent: URLs already inside an <a>...</a> element or an attribute value
// (href/src/content/cite/action) are left untouched, so re-runs are no-ops.
// Only the HTML part needs this — text/plain emails keep bare URLs, which is
// the correct standard form for them.
function linkifyHtml(html) {
  const masks = [];
  const hold = (m) => {
    masks.push(m);
    return `\u0000${masks.length - 1}\u0000`;
  };
  let masked = String(html || '');
  // Protect existing anchors and attribute values from re-wrapping.
  masked = masked.replace(/<a\b[^>]*>[\s\S]*?<\/a>/gi, hold);
  masked = masked.replace(/(?:href|src|content|cite|action)\s*=\s*["'][^"']*["']/gi, hold);
  // Wrap bare URLs (stop at whitespace, quotes, angle brackets, brackets/parens,
  // and the mask placeholder so a URL directly next to an anchor stays intact).
  masked = masked.replace(/(https?:\/\/[^\s"'<>()[\]{}\u0000]+)/g, (m) => {
    let u = m;
    while (u.length && /[.,;:!?]$/.test(u)) u = u.slice(0, -1); // drop trailing sentence punctuation
    return `<a href="${u}">${u}</a>`;
  });
  return masked.replace(/\u0000(\d+)\u0000/g, (_, i) => masks[Number(i)]);
}

module.exports = { internalize, linkifyHtml, InternalizeError, isApiToken };