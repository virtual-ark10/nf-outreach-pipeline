// Resend Pad — hardened server (zero dependencies)
// - Server-side Resend API key (never exposed to browsers)
// - Bearer token auth (PAD_TOKEN) on all /api/* except webhooks
// - In-memory rate limiting, 5MB body cap, no wildcard CORS
// - Phase 2: Received Emails API (GET /api/received, GET /api/received/:id)
// - Phase 3: webhook archive with Svix signature verification -> data/webhooks.jsonl

const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const PORT = parseInt(process.env.PORT || '3001', 10);
const RESEND_API_KEY = process.env.RESEND_API_KEY || '';
const PAD_TOKEN = process.env.PAD_TOKEN || '';
// Leads engine (the CRM behind the pad's Leads tab). Internal only — the pad
// proxies to it so one token (the pad token) unlocks both surfaces.
const CRM_PORT = parseInt(process.env.CRM_PORT || '3002', 10);
const CRM_HOST = process.env.CRM_HOST || '127.0.0.1';
const WEBHOOK_SECRET = process.env.RESEND_WEBHOOK_SECRET || '';
const DATA_DIR = process.env.DATA_DIR || path.join(__dirname, 'data');
const MAX_BODY = 5 * 1024 * 1024; // 5MB
const API_LIMIT = 60; // requests per window per IP
const API_WINDOW_MS = 60 * 1000;

// This pad is NewsletterFIT's surface, but Resend accounts are shared across
// brands: the sent/receiving APIs return EVERY brand's mail on the same API key.
// So the lists are filtered to this brand's domains (override with PAD_DOMAINS).
const BRAND_DOMAINS = (process.env.PAD_DOMAINS || 'newsletterfit.com')
  .split(',').map((s) => s.trim().toLowerCase()).filter(Boolean);

function addrList(v) {
  const s = Array.isArray(v) ? v.join(' ') : String(v || '');
  return (s.match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+/g) || []).map((a) => a.toLowerCase());
}
function isBrandAddr(addr) {
  return BRAND_DOMAINS.some((d) => addr.endsWith('@' + d));
}
// side: 'from' (sent mail) or 'to' (received mail). Items without a usable
// address are kept — better to show a mystery than to hide a real message.
function filterBrand(body, side) {
  const items = body && Array.isArray(body.data) ? body.data : null;
  if (!items) return { body, hidden: 0 };
  const keep = [];
  let hidden = 0;
  for (const m of items) {
    const addrs = side === 'from' ? addrList(m.from) : addrList(m.to).concat(addrList(m.received_for));
    if (!addrs.length || addrs.some(isBrandAddr)) keep.push(m);
    else hidden++;
  }
  return { body: Object.assign({}, body, { data: keep, brand: BRAND_DOMAINS, hidden_other_brand: hidden }), hidden };
}

// Outreach link minting (send-time internalization) — see outreach-links.cjs.
const outreach = require('./outreach-links.cjs');
const OUTREACH_CONF = {
  apiBase: process.env.NEWSLETTERFIT_API || 'http://127.0.0.1:3000/api/v1',
  token: process.env.API_BEARER_TOKEN || '',
  storePath: process.env.ATTRIBUTION_STORE || '/home/boxed/newsletterfit/attribution/attribution.json',
  baseUrl: 'https://newsletterfit.com',
};

if (!RESEND_API_KEY) console.warn('[WARN] RESEND_API_KEY not set — send/list endpoints will 503');
if (!PAD_TOKEN) console.warn('[WARN] PAD_TOKEN not set — /api/* (except webhook) will reject with 503');

const db = require('./db.cjs');
const P = require('./pipeline.cjs');

// ---------------------------------------------------------------- store
// Drafts, the send log and the webhook archive used to be three files
// (drafts.json, sent-drafts.jsonl, webhooks.jsonl). They are now rows in the
// shared SQLite store, so the pad and the leads engine read one source of truth.
//
// A 'discarded' or 'sent' draft is kept with its status, never spliced out of a
// list: the queue is a view (status = 'draft') and the history survives.
function draftToApi(r) {
  if (!r) return null;
  return {
    id: r.id, company: r.company, to: r.to_addr, cc: r.cc, from: r.from_addr,
    reply_to: r.reply_to, subject: r.subject, text: r.body_text, html: r.body_html,
    status: r.status, lead_id: r.lead_id, campaign: r.campaign,
    created_at: r.created_at, updated_at: r.updated_at, sent_at: r.sent_at,
    discarded_at: r.discarded_at, resend_id: r.resend_id, error: r.send_error,
  };
}
function readDrafts() {
  return db.all("SELECT * FROM drafts WHERE status = 'draft' ORDER BY created_at DESC").map(draftToApi);
}
function getDraft(id) {
  return db.one('SELECT * FROM drafts WHERE id = ?', [id]);
}
function upsertDraft(d) {
  const lead = db.findLeadByAddress(db.csvList(d.to)) || (d.id ? db.one('SELECT id FROM leads WHERE id = ?', [d.id]) : null);
  db.run(
    `INSERT INTO drafts (id, lead_id, company, from_addr, to_addr, cc, reply_to, subject, body_text, body_html, status, campaign, created_at, updated_at)
     VALUES (?,?,?,?,?,?,?,?,?,?,'draft',?,?,?)
     ON CONFLICT(id) DO UPDATE SET
       company = COALESCE(excluded.company, drafts.company),
       from_addr = COALESCE(excluded.from_addr, drafts.from_addr),
       to_addr = COALESCE(excluded.to_addr, drafts.to_addr),
       cc = COALESCE(excluded.cc, drafts.cc),
       reply_to = COALESCE(excluded.reply_to, drafts.reply_to),
       subject = COALESCE(excluded.subject, drafts.subject),
       body_text = COALESCE(excluded.body_text, drafts.body_text),
       body_html = COALESCE(excluded.body_html, drafts.body_html),
       updated_at = excluded.updated_at`,
    [d.id, lead ? lead.id : null, d.company || null, d.from || null, db.csvList(d.to), db.csvList(d.cc),
     db.csvList(d.reply_to), d.subject || null, d.text || null, d.html || null,
     d.campaign || null, d.created_at || db.nowISO(), db.nowISO()]
  );
  return getDraft(d.id);
}
// The send of record for a draft: status + resend_id, and the lead-side record
// (emails row + stage move) so the CRM is correct without waiting for a sync.
function markDraftSent(draft, { resendId, subject, text, html, to }) {
  const at = db.nowISO();
  return db.tx(() => {
    db.run("UPDATE drafts SET status = 'sent', sent_at = ?, resend_id = ?, send_error = NULL, updated_at = ? WHERE id = ?",
      [at, resendId || null, at, draft.id]);
    db.logEvent({ entity: 'draft', entity_id: draft.id, type: 'draft_sent', payload: { resend_id: resendId, subject: subject || draft.subject }, at, actor: 'pad' });
    const lead = (draft.lead_id ? db.one('SELECT * FROM leads WHERE id = ?', [draft.lead_id]) : null)
      || db.findLeadByAddress(db.csvList(to || draft.to_addr));
    if (!lead) return { sent: true, lead_id: null };
    const out = P.recordOutbound({
      lead, subject: subject || draft.subject, to: to || draft.to_addr,
      text: text || draft.body_text, html: html || draft.body_html, resendId, campaign: draft.campaign,
    });
    db.logEvent({ entity: 'lead', entity_id: lead.id, type: 'send', payload: { draft_id: draft.id, resend_id: resendId, stage: out.stage }, at, actor: 'pad' });
    return { sent: true, lead_id: lead.id, stage: out.stage, advanced: out.advanced };
  });
}
function markDraftDiscarded(id) {
  const at = db.nowISO();
  db.run("UPDATE drafts SET status = 'discarded', discarded_at = ?, updated_at = ? WHERE id = ? AND status = 'draft'", [at, at, id]);
  db.logEvent({ entity: 'draft', entity_id: id, type: 'draft_discarded', payload: {}, at, actor: 'pad' });
  return getDraft(id);
}
// Resend webhook -> tables. Never a raw append-only file again: a delivery receipt
// updates the message it refers to, an inbound message becomes a reply.
function handleWebhook(ev, receivedAt) {
  const type = ev.type || 'unknown';
  const d = ev.data || {};
  const at = d.created_at || receivedAt || db.nowISO();
  const out = { type, stored: null, skipped: null };
  db.logEvent({ entity: 'webhook', entity_id: d.email_id || d.message_id || null, type, payload: ev, at: receivedAt || at, actor: 'resend' });
  const addrs = [].concat(d.to || [], d.from || [], d.received_for || []);
  if (!P.isBrandMail(...addrs)) { out.skipped = 'another brand on the shared Resend account'; return out; }
  if (type === 'email.received') {
    const r = P.recordInbound({
      from: d.from, to: d.received_for || d.to, subject: d.subject,
      text: d.text, html: d.html, resendId: d.email_id, messageId: d.message_id,
      inReplyTo: d.in_reply_to || null, at, raw: ev,
    });
    out.stored = { reply_id: r.reply_id, lead_id: r.lead_id, duplicate: Boolean(r.duplicate) };
  } else if (/^email\.(delivered|bounced|complained|failed|opened|clicked)$/.test(type)) {
    out.stored = P.recordDeliveryStatus({ resendId: d.email_id, status: type.split('.')[1], to: d.to, from: d.from, at });
  }
  return out;
}

const rateBuckets = new Map(); // ip -> { count, resetAt }

function rateCheck(ip) {
  const now = Date.now();
  let b = rateBuckets.get(ip);
  if (!b || b.resetAt <= now) {
    b = { count: 0, resetAt: now + API_WINDOW_MS };
    rateBuckets.set(ip, b);
  }
  b.count += 1;
  return b.count <= API_LIMIT;
}

function sendJson(res, status, obj) {
  const body = JSON.stringify(obj);
  console.log(`  -> ${status} ${String(res.reqPath || '?')}`);
  res.writeHead(status, {
    'Content-Type': 'application/json',
    'X-Content-Type-Options': 'nosniff',
    'Cache-Control': 'no-store',
  });
  res.end(body);
}

function forbidden(res, msg) {
  sendJson(res, 403, { error: msg });
}

// ---- Resend outbound relay (server-side key) ----
function resendRequest(method, apiPath, body, cb) {
  const options = {
    hostname: 'api.resend.com',
    path: apiPath,
    method,
    headers: { 'Authorization': `Bearer ${RESEND_API_KEY}` },
    timeout: 30000,
  };
  if (body) {
    options.headers['Content-Type'] = 'application/json';
    options.headers['Content-Length'] = Buffer.byteLength(body);
  }
  const req = https.request(options, (res) => {
    let data = '';
    res.on('data', (c) => { data += c; if (data.length > 8 * 1024 * 1024) req.destroy(); });
    res.on('end', () => cb(null, res.statusCode, data));
  });
  req.on('timeout', () => req.destroy(new Error('Resend API timeout')));
  req.on('error', (e) => cb(e));
  if (body) req.write(body);
  req.end();
}

// ---- Svix webhook signature verification (Resend webhooks) ----
function verifyWebhook(rawBody, headers) {
  if (!WEBHOOK_SECRET) {
    return { ok: false, reason: 'RESEND_WEBHOOK_SECRET not configured' };
  }
  const id = headers['svix-id'] || headers['Svix-Id'];
  const ts = headers['svix-timestamp'] || headers['Svix-Timestamp'];
  const sigHeader = headers['svix-signature'] || headers['Svix-Signature'];
  if (!id || !ts || !sigHeader) return { ok: false, reason: 'missing svix headers' };

  const now = Math.floor(Date.now() / 1000);
  const tsNum = parseInt(ts, 10);
  if (!tsNum || Math.abs(now - tsNum) > 300) return { ok: false, reason: 'timestamp outside tolerance' };

  const secret = WEBHOOK_SECRET.startsWith('whsec_') ? WEBHOOK_SECRET.slice(6) : WEBHOOK_SECRET;
  let secretBytes;
  try { secretBytes = Buffer.from(secret, 'base64'); } catch { return { ok: false, reason: 'bad secret' }; }

  const signedContent = `${id}.${ts}.${rawBody}`;
  const expected = crypto.createHmac('sha256', secretBytes).update(signedContent).digest('base64');

  const provided = sigHeader.split(' ').map((part) => {
    const i = part.indexOf(',');
    return i >= 0 ? part.slice(i + 1) : part;
  }).filter(Boolean);

  const ok = provided.some((sig) => {
    if (!sig || sig.length !== expected.length) return false;
    return crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(expected));
  });
  return ok ? { ok: true } : { ok: false, reason: 'signature mismatch' };
}

function appendWebhookLog(entry) {
  const line = JSON.stringify(entry);
  return new Promise((resolve) => {
    fs.appendFile(WEBHOOK_LOG, line + '\n', (err) => resolve(!err));
  });
}

function readBody(req, res, cb) {
  let size = 0;
  const chunks = [];
  req.on('data', (c) => {
    size += c.length;
    if (size > MAX_BODY) {
      sendJson(res, 413, { error: 'Payload too large' });
      req.destroy();
      return;
    }
    chunks.push(c);
  });
  req.on('end', () => cb(Buffer.concat(chunks).toString('utf8'), size));
  req.on('error', () => { /* client aborted */ });
}

const server = http.createServer((req, res) => {
  const url = req.url || '/';
  const ip = req.socket.remoteAddress || 'unknown';
  res.reqPath = `${req.method} ${url}`;
  console.log(`[${new Date().toISOString()}] ${res.reqPath} (${ip})`);

  // Security headers for everything
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('X-Frame-Options', 'DENY');
  res.setHeader('Referrer-Policy', 'same-origin');
  res.setHeader('Content-Security-Policy',
    "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net https://cdn.tailwindcss.com; " +
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdn.tailwindcss.com; " +
    "img-src 'self' data: https:; connect-src 'self'; font-src 'self' data: https://fonts.gstatic.com; " +
    "frame-ancestors 'none'; base-uri 'self'");

  // ---- API routes ----
  if (url.startsWith('/api/')) {
    return handleApi(req, res, url, ip);
  }

  // ---- Static files ----
  let filePath = url === '/' ? '/index.html' : url;
  filePath = path.join(__dirname, filePath);
  const realPath = path.resolve(filePath);
  const baseDir = path.resolve(__dirname);
  if (!realPath.startsWith(baseDir)) {
    console.log('[SECURITY] Directory traversal blocked:', url);
    return forbidden(res, 'Forbidden');
  }
  fs.readFile(realPath, (err, data) => {
    if (err) {
      res.writeHead(404, { 'Content-Type': 'text/plain' });
      return res.end('404 Not Found');
    }
    let ct = 'text/plain';
    if (realPath.endsWith('.html')) ct = 'text/html';
    else if (realPath.endsWith('.css')) ct = 'text/css';
    else if (realPath.endsWith('.js')) ct = 'application/javascript';
    else if (realPath.endsWith('.json')) ct = 'application/json';
    else if (realPath.endsWith('.svg')) ct = 'image/svg+xml';
    else if (realPath.endsWith('.png')) ct = 'image/png';
    else if (realPath.endsWith('.ico')) ct = 'image/x-icon';
    else if (realPath.endsWith('.webmanifest')) ct = 'application/manifest+json';
    res.writeHead(200, { 'Content-Type': ct, 'Cache-Control': 'no-cache' });
    res.end(data);
  });
});

function proxyCrm(req, res, targetPath) {
  const chunks = [];
  req.on('data', (c) => chunks.push(c));
  req.on('end', () => {
    const up = http.request({
      host: CRM_HOST, port: CRM_PORT, path: targetPath, method: req.method,
      headers: { 'X-CRM-Token': PAD_TOKEN, 'Content-Type': 'application/json' },
    }, (r) => {
      res.writeHead(r.statusCode || 502, {
        'Content-Type': r.headers['content-type'] || 'application/json',
        'Cache-Control': 'no-store',
      });
      r.pipe(res);
    });
    up.on('error', () => sendJson(res, 502, { error: 'Leads engine unavailable — check the nf-crm service' }));
    if (chunks.length) up.write(Buffer.concat(chunks));
    up.end();
  });
}

function handleApi(req, res, url, ip) {
  const p = url.split('?')[0]; // path without query string
  // Rate limit all API routes
  if (!rateCheck(ip)) return sendJson(res, 429, { error: 'Rate limit exceeded — slow down' });

  // Health (no auth — used by watchdog)
  if (req.method === 'GET' && p === '/api/health') {
    return sendJson(res, 200, { ok: true, uptime: process.uptime() });
  }

  // Webhook receiver (phase 3) — auth via Svix signature, NOT PAD_TOKEN
  if (req.method === 'POST' && p === '/api/webhook') {
    return readBody(req, res, (rawBody) => {
      const v = verifyWebhook(rawBody, req.headers);
      if (!v.ok) {
        console.warn('[WEBHOOK] Rejected:', v.reason);
        return sendJson(res, 400, { error: `Invalid webhook: ${v.reason}` });
      }
      let event;
      try { event = JSON.parse(rawBody); } catch { return sendJson(res, 400, { error: 'bad json' }); }
      try {
        const r = handleWebhook(event, new Date().toISOString());
        console.log(`[WEBHOOK] ${r.type} stored=${JSON.stringify(r.stored)}${r.skipped ? ' skipped=' + r.skipped : ''}`);
        return sendJson(res, 200, { ok: true, type: r.type, stored: r.stored, skipped: r.skipped });
      } catch (e) {
        console.error('[WEBHOOK] store failed:', e && e.message);
        return sendJson(res, 500, { error: 'store failed: ' + (e && e.message) });
      }
    });
  }

  // Public, secret-free UI config so branding is env-driven, not hardcoded.
  // Lets the same checkout be re-badged for another project without editing HTML.
  if (req.method === 'GET' && p === '/api/config') {
    return sendJson(res, 200, {
      brand: process.env.BRAND_NAME || '',
      from_email: process.env.FROM_EMAIL || '',
      domains: BRAND_DOMAINS,
      base_url: process.env.PUBLIC_BASE_URL || '',
    });
  }

  // Everything else requires PAD_TOKEN
  const auth = req.headers['x-pad-token'];
  if (!PAD_TOKEN || auth !== PAD_TOKEN) {
    const mask = (s) => s ? s.slice(0, 4) + '…' + s.slice(-4) : '(none)';
    console.log(`[AUTH-FAIL] ${req.method} ${url} got=${mask(auth)} expected=${mask(PAD_TOKEN)}`);
    return sendJson(res, 401, { error: 'Unauthorized — missing or invalid token' });
  }

  // Leads tab: /api/crm/* -> the leads engine, authorised by the pad token we
  // just verified. The browser never sees or sends a second token.
  if (p === '/api/crm' || p.startsWith('/api/crm/')) {
    return proxyCrm(req, res, '/api' + p.slice('/api/crm'.length));
  }

  if (req.method === 'POST' && p === '/api/send') {
    return readBody(req, res, (body, size) => {
      let data;
      try { data = JSON.parse(body); } catch { return sendJson(res, 400, { error: 'Invalid JSON' }); }
      if (!data.from) return sendJson(res, 400, { error: 'from is required' });
      const leadId = 'manual-' + (String((Array.isArray(data.to) ? data.to[0] : data.to) || 'unknown').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-') || 'unknown');
      const doSend = () => resendRequest('POST', '/emails', JSON.stringify(data), (err, status, rbody) => {
        if (err) return sendJson(res, 502, { error: 'Failed to contact Resend', details: err.message });
        if (status === 200 || status === 201) {
          console.log('[SEND] Email accepted, id:', rbody.slice(0, 200));
          // Log it against the lead here too: whichever process records first wins,
          // and the emails.resend_id unique index makes the other a no-op.
          try {
            const to = (String(data.to || '').match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/) || [])[0];
            const lead = db.findLeadByAddress(to);
            if (lead) {
              const resendId = (safeJson(rbody) || {}).id || null;
              const out = P.recordOutbound({ lead, subject: data.subject, to: data.to, text: data.text, html: data.html, resendId, campaign: data.campaign });
              db.logEvent({ entity: 'lead', entity_id: lead.id, type: 'send', payload: { resend_id: resendId, subject: data.subject || '', stage: out.stage }, at: db.nowISO(), actor: 'pad' });
            }
          } catch (e) { console.error('[SEND] lead record failed:', e && e.message); }
        }
        sendJson(res, status, safeJson(rbody));
      });
      const rawText = data.text || '';
      const rawHtml = data.html || '';
      if (!rawText.trim() && !rawHtml.trim()) return doSend();
      outreach.internalize(rawText, rawHtml, leadId, OUTREACH_CONF).then((out) => {
        data.text = out.text;
        data.html = outreach.linkifyHtml(out.html); // bare URL -> <a href> in the html body
        if (out.minted.length) console.log(`[SEND] Internalized ${out.minted.length} link(s) for ${leadId} at send time`);
        doSend();
      }).catch((e) => {
        console.log(`[SEND] BLOCKED ${leadId}: ${e.message}`);
        sendJson(res, 400, { error: `Not sent: ${e.message}` });
      });
    });
  }

  if (req.method === 'GET' && p === '/api/domains') {
    return resendRequest('GET', '/domains', null, (err, status, rbody) => {
      if (err) return sendJson(res, 502, { error: 'Failed to contact Resend', details: err.message });
      sendJson(res, status, safeJson(rbody));
    });
  }

  if (req.method === 'GET' && p.startsWith('/api/email/')) {
    const id = p.slice('/api/email/'.length);
    return resendRequest('GET', `/emails/${encodeURIComponent(id)}`, null, (err, status, rbody) => {
      if (err) return sendJson(res, 502, { error: 'Failed to contact Resend', details: err.message });
      sendJson(res, status, safeJson(rbody));
    });
  }

  if (req.method === 'GET' && p.startsWith('/api/sent')) {
    const page = new URL(url, 'http://x').searchParams.get('page') || '1';
    return resendRequest('GET', `/emails?page=${encodeURIComponent(page)}`, null, (err, status, rbody) => {
      if (err) return sendJson(res, 502, { error: 'Failed to contact Resend', details: err.message });
      const body = safeJson(rbody);
      const f = filterBrand(body, 'from');   // only this brand's senders
      sendJson(res, status, f.body);
    });
  }

  // Phase 2: received emails
  if (req.method === 'GET' && p === '/api/received') {
    const q = new URL(url, 'http://x');
    const limit = q.searchParams.get('limit') || '50';
    const after = q.searchParams.get('after') || '';
    const before = q.searchParams.get('before') || '';
    let p = `/emails/receiving?limit=${encodeURIComponent(limit)}`;
    if (after) p += `&after=${encodeURIComponent(after)}`;
    if (before) p += `&before=${encodeURIComponent(before)}`;
    return resendRequest('GET', p, null, (err, status, rbody) => {
      if (err) return sendJson(res, 502, { error: 'Failed to contact Resend', details: err.message });
      const body = safeJson(rbody);
      const f = filterBrand(body, 'to');     // only mail addressed to this brand
      sendJson(res, status, f.body);
    });
  }

  if (req.method === 'GET' && p.startsWith('/api/received/')) {
    const id = p.slice('/api/received/'.length);
    return resendRequest('GET', `/emails/receiving/${encodeURIComponent(id)}`, null, (err, status, rbody) => {
      if (err) return sendJson(res, 502, { error: 'Failed to contact Resend', details: err.message });
      sendJson(res, status, safeJson(rbody));
    });
  }

  // Phase 3: webhook archive (read back from the events table, most recent first)
  if (req.method === 'GET' && p === '/api/archive') {
    const limit = parseInt(new URL(url, 'http://x').searchParams.get('limit') || '50', 10);
    const rows = db.all("SELECT at, type, entity_id, payload FROM events WHERE entity = 'webhook' ORDER BY at DESC LIMIT ?", [limit]);
    return sendJson(res, 200, { data: rows.map((r) => ({ received_at: r.at, type: r.type, email_id: r.entity_id, event: db.pj(r.payload, {}) })) });
  }

  // Phase 4: draft queue (review-before-send) — rows in `drafts`, status='draft'
  if (req.method === 'GET' && p === '/api/drafts') {
    return sendJson(res, 200, { data: readDrafts() });
  }

  if ((req.method === 'PUT' || req.method === 'POST') && p.startsWith('/api/drafts/')) {
    const rawId = p.slice('/api/drafts/'.length);
    const isSendRoute = rawId.endsWith('/send');
    if (isSendRoute) {
      const draftId = decodeURIComponent(rawId.slice(0, -'/send'.length));
      return readBody(req, res, (body, size) => {
        let data;
        try { data = JSON.parse(body); } catch { return sendJson(res, 400, { error: 'Invalid JSON' }); }
        const stored = getDraft(draftId);
        if (!stored) return sendJson(res, 404, { error: 'Draft not found' });
        if (stored.status !== 'draft') return sendJson(res, 409, { error: `Draft is already ${stored.status}` });
        // Merge any client edits over the stored draft
        const draft = Object.assign({}, draftToApi(stored), data || {});
        const payload = {
          from: draft.from || data.from,
          to: String(draft.to || '').split(',').map((s) => s.trim()).filter(Boolean),
          cc: String(draft.cc || '').split(',').map((s) => s.trim()).filter(Boolean),
          subject: draft.subject || '',
          html: draft.html || '',
          text: draft.text || '',
        };
        // reply_to: client sends an array already; stored drafts keep a string
        if (Array.isArray(draft.reply_to)) payload.reply_to = draft.reply_to;
        else if (typeof draft.reply_to === 'string' && draft.reply_to.trim()) payload.reply_to = [draft.reply_to.trim()];
        if (draft.attachments && Array.isArray(draft.attachments)) payload.attachments = draft.attachments;
        if (draft.headers) payload.headers = draft.headers;
        if (!payload.from || payload.to.length === 0 || !payload.subject) {
          return sendJson(res, 400, { error: 'Draft is incomplete (from/to/subject required)' });
        }
        const doSendDraft = () => resendRequest('POST', '/emails', JSON.stringify(payload), (err, status, rbody) => {
          if (err) {
            db.run('UPDATE drafts SET send_error = ?, updated_at = ? WHERE id = ?', [err.message, db.nowISO(), draftId]);
            return sendJson(res, 502, { error: 'Failed to contact Resend', details: err.message });
          }
          if (status === 200 || status === 201) {
            const resendId = (safeJson(rbody) || {}).id || null;
            const out = markDraftSent(stored, { resendId, subject: payload.subject, text: payload.text, html: payload.html, to: payload.to });
            console.log(`[DRAFT] Sent + archived: ${draftId} (${stored.company || payload.to}) -> lead=${out.lead_id || 'none'} stage=${out.stage || '-'}`);
          } else {
            db.run('UPDATE drafts SET send_error = ?, updated_at = ? WHERE id = ?', [String(rbody).slice(0, 500), db.nowISO(), draftId]);
          }
          sendJson(res, status, safeJson(rbody));
        });
        const rawText = payload.text || '';
        const rawHtml = payload.html || '';
        if (!rawText.trim() && !rawHtml.trim()) return doSendDraft();
        // Send-time internalization: re-mint dead old-format tokens server-side
        // and block external links (policy). NEVER send broken/leaky links.
        outreach.internalize(rawText, rawHtml, draftId, OUTREACH_CONF).then((out) => {
          const finalHtml = outreach.linkifyHtml(out.html); // bare URL -> <a href>
          payload.text = out.text;
          payload.html = finalHtml;
          if (out.minted.length) {
            console.log(`[DRAFT] Internalized ${out.minted.length} link(s) for ${draftId} at send time`);
            // Persist the rewritten draft so the queue shows the final links
            // even if Resend rejects the send.
            db.run('UPDATE drafts SET body_text = ?, body_html = ?, updated_at = ? WHERE id = ?', [out.text, finalHtml, db.nowISO(), draftId]);
          }
          doSendDraft();
        }).catch((e) => {
          console.log(`[DRAFT] BLOCKED ${draftId}: ${e.message}`);
          sendJson(res, 400, { error: `Not sent: ${e.message}` });
        });
      });
    }
    // PUT /api/drafts/:id — save edits to an existing draft
    if (req.method === 'PUT') {
      const id = decodeURIComponent(rawId);
      return readBody(req, res, (body, size) => {
        let data;
        try { data = JSON.parse(body); } catch { return sendJson(res, 400, { error: 'Invalid JSON' }); }
        const stored = getDraft(id);
        if (!stored) return sendJson(res, 404, { error: 'Draft not found' });
        const m = Object.assign({}, draftToApi(stored), data || {});
        upsertDraft({
          id, company: m.company, from: m.from, to: m.to, cc: m.cc, reply_to: m.reply_to,
          subject: m.subject, text: m.text, html: m.html, campaign: m.campaign,
          created_at: stored.created_at,
        });
        return sendJson(res, 200, { ok: true, id });
      });
    }
    return sendJson(res, 405, { error: 'Method not allowed' });
  }

  // DELETE /api/drafts/:id — discard, not delete: the row keeps its history.
  if (req.method === 'DELETE' && p.startsWith('/api/drafts/')) {
    const id = decodeURIComponent(p.slice('/api/drafts/'.length));
    const stored = getDraft(id);
    if (!stored || stored.status !== 'draft') return sendJson(res, 404, { error: 'Draft not found' });
    markDraftDiscarded(id);
    console.log(`[DRAFT] Discarded: ${id}`);
    return sendJson(res, 200, { ok: true, id, status: 'discarded' });
  }

  return sendJson(res, 404, { error: 'Not found' });
}

function safeJson(raw) {
  try { return JSON.parse(raw); } catch { return { raw }; }
}

server.listen(PORT, () => {
  console.log(`✓ Resend Pad running on http://127.0.0.1:${PORT}`);
  console.log(`  db: ${db.DB_PATH}`);
  console.log(`  POST /api/send | GET /api/domains | GET /api/sent | GET /api/received | POST /api/webhook | GET /api/archive | GET /api/drafts | POST /api/drafts/:id/send | PUT/DELETE /api/drafts/:id`);
});