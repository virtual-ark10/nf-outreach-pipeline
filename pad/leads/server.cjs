'use strict';
// ============================================================================
//  leads/server.cjs — the CRM API behind the pad's Leads tab.
//
//  This file is the HTTP layer only: routing, auth, rate limiting. The stage
//  machine and everything a send/reply does to a lead live in ../pipeline.cjs,
//  and storage is ../db.cjs (SQLite via node:sqlite, schema in ../schema.sql).
//
//  API (header X-CRM-Token: the pad token). Field names are unchanged from the
//  JSON-store version so the pad UI and the agent skills keep working.
//
//   GET    /api/health                 liveness + lead count
//   GET    /api/meta                   brand, stages, per-stage counts, last sync
//   GET    /api/leads[?stage=]         every lead, decorated
//   POST   /api/leads                  create (409 on duplicate id)
//   GET    /api/leads/:id              lead + its activity stream
//   PATCH  /api/leads/:id              stage / contact / priority / deal value ...
//   GET    /api/leads/:id/timeline     v_lead_timeline rows for that lead
//   POST   /api/leads/:id/note         append a note
//   GET    /api/pipeline               v_lead_pipeline (reporting view)
//   GET    /api/followups-due          v_followups_due (who is due a touch)
//   GET    /api/emails[?lead_id=]      raw emails rows
//   GET    /api/replies[?lead_id=]     inbound replies
//   PATCH  /api/replies/:id            is_read / starred / classification / sentiment
//   DELETE /api/replies/:id            soft delete (the ✕)
//   POST   /api/sync                   reconcile pad mail into the CRM
//   POST   /api/email/send             send via the pad, log it, advance the stage
//   GET    /api/email/inbox|sent|drafts
//   POST   /api/email/drafts/:id/send  send a pad draft, log it, advance the stage
// ============================================================================

const http = require('http');
const path = require('path');
const db = require('../db.cjs');
const P = require('../pipeline.cjs');

const PORT = parseInt(process.env.CRM_PORT || process.env.PORT || '3002', 10);
const HOST = process.env.CRM_HOST || '0.0.0.0';
const TOKEN = process.env.CRM_TOKEN || process.env.PAD_TOKEN || '';
const SERVICE = process.env.SERVICE_NAME || 'nf-crm';
const BRAND = process.env.BRAND_NAME || '';
const BODY_CAP = 1024 * 1024;
const STAGE_KEYS = P.STAGE_KEYS;

function send(res, code, obj, type) {
  const body = type ? obj : JSON.stringify(obj);
  res.writeHead(code, {
    'Content-Type': type || 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
  });
  res.end(body);
}
function readBody(req) {
  return new Promise((resolve, reject) => {
    let n = 0; const chunks = [];
    req.on('data', (c) => { n += c.length; if (n > BODY_CAP) { reject(new Error('body too large')); req.destroy(); } else chunks.push(c); });
    req.on('end', () => { try { resolve(chunks.length ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : {}); } catch (e) { reject(new Error('invalid json')); } });
    req.on('error', reject);
  });
}
function nameFromEmail(email) {
  const local = String(email || '').split('@')[0] || '';
  if (!local || /^(info|hello|contact|team|sales|admin|support|hi)$/i.test(local)) return '';
  return local.split(/[._-]+/).filter(Boolean).map((p) => p.charAt(0).toUpperCase() + p.slice(1)).join(' ');
}
// API field -> DB column. Both the schema name and the legacy API name are accepted.
const EDIT_COLS = {
  contact_name: 'contact_name', contact_title: 'contact_title', contact_role: 'contact_role',
  domain: 'domain', website: 'website', email: 'email', contact_email: 'email',
 extra_emails: 'emails', emails: 'emails',
  priority: 'priority', quality: 'priority', score: 'score', owner: 'owner',
  industry: 'industry', city: 'city', region: 'region', country: 'country', source: 'source',
  campaign: 'campaign', angle: 'angle', subscriber_range: 'subscriber_range',
  next_follow_up_at: 'next_follow_up_at', next_action_at: 'next_follow_up_at',
  converted: 'converted', converted_at: 'converted_at', value_cents: 'value_cents', currency: 'currency',
  unsubscribed: 'unsubscribed', bounced: 'bounced',
  notes: 'notes', tags: 'tags', sponsored_pubs: 'sponsored_pubs', recommended_pubs: 'recommended_pubs',
  meta: 'meta',
};
const JSON_COLS = new Set(['emails', 'notes', 'tags', 'sponsored_pubs', 'recommended_pubs', 'meta']);
const BIT_COLS = new Set(['converted', 'unsubscribed', 'bounced']);

// Columns a new lead is created with. Placeholders are derived from this list so a
// hand-counted VALUES() list can never drift out of step with it.
const NEW_LEAD_COLS = [
  'id', 'company', 'domain', 'website', 'contact_name', 'contact_title', 'contact_role',
  'email', 'emails', 'industry', 'city', 'region', 'country', 'source', 'stage',
  'priority', 'score', 'owner', 'tags', 'notes', 'campaign', 'sponsored_pubs',
  'recommended_pubs', 'angle', 'subscriber_range', 'meta', 'currency', 'created_at', 'updated_at',
];
const NEW_LEAD_SQL = `INSERT INTO leads (${NEW_LEAD_COLS.join(', ')})
                      VALUES (${NEW_LEAD_COLS.map(() => '?').join(', ')})`;

const HITS = new Map();
function rateLimited(ip) {
  const now = Date.now();
  const e = HITS.get(ip) || { n: 0, t: now };
  if (now - e.t > 60000) { e.n = 0; e.t = now; }
  e.n++; HITS.set(ip, e);
  if (HITS.size > 500) HITS.clear();
  return e.n > 120;
}

const server = http.createServer(async (req, res) => {
  const url = req.url || '/';
  const p = url.split('?')[0];
  const q = new URL(url, 'http://x').searchParams;
  const ip = (req.headers['x-forwarded-for'] || req.socket.remoteAddress || '?').split(',')[0].trim();

  if (rateLimited(ip)) return send(res, 429, { error: 'too many requests' });

  if (p === '/api/health') {
    return send(res, 200, {
      ok: true, service: SERVICE,
      leads: db.val('SELECT COUNT(*) FROM leads WHERE deleted_at IS NULL'),
      db: path.basename(db.DB_PATH),
    });
  }
  // There is no standalone page: the Leads tab inside the pad is the only UI.
  if ((p === '/' || p === '/index.html') && (req.method === 'GET' || req.method === 'HEAD')) {
    return send(res, 200, 'The leads engine has no UI of its own — open the pad and use its Leads tab.', 'text/plain; charset=utf-8');
  }
  if (!TOKEN) return send(res, 500, { error: 'server missing CRM_TOKEN/PAD_TOKEN' });
  if (req.headers['x-crm-token'] !== TOKEN) return send(res, 401, { error: 'unauthorized' });

  try {
    // ---------------- meta
    if (p === '/api/meta' && req.method === 'GET') {
      const counts = {};
      STAGE_KEYS.forEach((k) => {
        counts[k] = db.val('SELECT COUNT(*) FROM leads WHERE stage = ? AND deleted_at IS NULL AND archived_at IS NULL', [k]) || 0;
      });
      return send(res, 200, {
        brand: BRAND, stages: P.STAGES, counts,
        total: db.val('SELECT COUNT(*) FROM leads WHERE deleted_at IS NULL AND archived_at IS NULL'),
        converted: db.val('SELECT COUNT(*) FROM leads WHERE converted = 1 AND deleted_at IS NULL'),
        sync: { email: db.val("SELECT MAX(at) FROM events WHERE type = 'sync'") },
        storage: { engine: 'sqlite (node:sqlite)', file: db.DB_PATH },
      });
    }

    // ---------------- leads
    if (p === '/api/leads' && req.method === 'GET') {
      let leads = db.all('SELECT * FROM leads WHERE deleted_at IS NULL ORDER BY created_at DESC').map(P.rowToLead);
      const stage = q.get('stage');
      if (stage) leads = leads.filter((l) => l.stage === stage);
      return send(res, 200, { leads });
    }
    if (p === '/api/leads' && req.method === 'POST') {
      const b = await readBody(req);
      if (!b.company) return send(res, 400, { error: 'company is required' });
      const id = b.id || P.kebab(b.company);
      if (db.one('SELECT id FROM leads WHERE id = ?', [id])) return send(res, 409, { error: 'lead already exists', id });
      const at = db.nowISO();
      const email = b.contact_email || b.email || null;
      const vals = {
        id,
        company: b.company,
        domain: b.domain || null,
        website: b.website || null,
        contact_name: b.contact_name || null,
        contact_title: b.contact_title || null,
        contact_role: b.contact_role || 'other',
        email,
        emails: db.j(b.extra_emails || b.emails || []),
        industry: b.industry || null,
        city: b.city || null,
        region: b.region || null,
        country: b.country || null,
        source: b.source || 'manual',
        stage: STAGE_KEYS.includes(b.stage) ? b.stage : 'leads',
        priority: b.priority || b.quality || null,
        score: db.int(b.score),
        owner: b.owner || null,
        tags: db.j(b.tags || []),
        notes: db.j(b.notes || []),
        campaign: b.campaign || null,
        sponsored_pubs: db.j(b.sponsored_pubs || []),
        recommended_pubs: db.j(b.recommended_pubs || []),
        angle: b.angle || null,
        subscriber_range: b.subscriber_range || null,
        meta: db.j(b.meta || {}),
        currency: b.currency || 'USD',
        created_at: at,
        updated_at: at,
      };
      db.run(NEW_LEAD_SQL, NEW_LEAD_COLS.map((c) => vals[c]));
      db.logEvent({ entity: 'lead', entity_id: id, type: 'created', payload: { company: b.company, source: b.source || 'manual' }, at, actor: 'crm' });
      return send(res, 201, { ok: true, lead: P.rowToLead(db.one('SELECT * FROM leads WHERE id = ?', [id])) });
    }

    if (p.startsWith('/api/leads/')) {
      const rest = p.slice('/api/leads/'.length).split('/');
      const id = decodeURIComponent(rest[0]);
      const row = db.one('SELECT * FROM leads WHERE id = ?', [id]);
      if (!row) return send(res, 404, { error: 'unknown lead' });

      if (rest[1] === 'note' && req.method === 'POST') {
        const b = await readBody(req);
        if (!b.body) return send(res, 400, { error: 'body is required' });
        db.logEvent({ entity: 'lead', entity_id: id, type: 'note', payload: { detail: String(b.body).slice(0, 2000) }, at: db.nowISO(), actor: 'crm' });
        return send(res, 201, { ok: true });
      }
      if (rest[1] === 'timeline' && req.method === 'GET') {
        return send(res, 200, { timeline: db.all('SELECT * FROM v_lead_timeline WHERE lead_id = ? ORDER BY at DESC', [id]) });
      }
      if (req.method === 'GET') {
        return send(res, 200, { lead: P.rowToLead(row), activity: P.activityFor(id) });
      }
      if (req.method === 'PATCH' || req.method === 'PUT') {
        const b = await readBody(req);
        if (b.stage && !STAGE_KEYS.includes(b.stage)) return send(res, 400, { error: 'bad stage', allowed: STAGE_KEYS });
        const at = db.nowISO();
        const before = row.stage;
        const sets = []; const vals = [];
        for (const [key, col] of Object.entries(EDIT_COLS)) {
          if (b[key] === undefined) continue;
          let v = b[key];
          if (BIT_COLS.has(col)) v = db.bit(v);
          else if (col === 'score' || col === 'value_cents') v = db.int(v);
          else if (JSON_COLS.has(col)) v = db.j(v);
          else if (col === 'email') v = (Array.isArray(v) ? v.join(', ') : String(v == null ? '' : v)) || null;
          else if (v !== null) v = String(v);
          sets.push(`${col} = ?`); vals.push(v);
        }
        if (sets.length) {
          sets.push('updated_at = ?'); vals.push(at, id);
          db.run(`UPDATE leads SET ${sets.join(', ')} WHERE id = ?`, vals);
        }
        // converted and its timestamp travel together, in both directions.
        if (b.converted !== undefined) {
          if (db.bit(b.converted)) {
            if (!b.converted_at) db.run('UPDATE leads SET converted_at = COALESCE(converted_at, ?) WHERE id = ?', [at, id]);
          } else {
            db.run('UPDATE leads SET converted_at = NULL WHERE id = ?', [id]);
          }
        }
        if (b.stage && b.stage !== before) {
          db.run('UPDATE leads SET stage = ?, updated_at = ?, stage_changed_at = ? WHERE id = ?', [b.stage, at, at, id]);
          db.stageEvent({ lead_id: id, from_stage: before, to_stage: b.stage, at, by: b.by || 'crm', note: b.note || 'stage changed in CRM', source: 'crm' });
        }
        if (b.archived === true || b.stage === 'archived') {
          db.run('UPDATE leads SET archived_at = COALESCE(archived_at, ?) WHERE id = ?', [at, id]);
        } else if (b.archived === false) {
          db.run('UPDATE leads SET archived_at = NULL WHERE id = ?', [id]);
        }
        if (b.deleted === true) db.run('UPDATE leads SET deleted_at = ? WHERE id = ?', [at, id]);
        return send(res, 200, { ok: true, lead: P.rowToLead(db.one('SELECT * FROM leads WHERE id = ?', [id])) });
      }
    }

    // ---------------- replies (the ✕ is a soft delete)
    if (p === '/api/replies' && req.method === 'GET') {
      const leadId = q.get('lead_id');
      const rows = leadId
        ? db.all('SELECT * FROM replies WHERE lead_id = ? AND deleted_at IS NULL ORDER BY received_at DESC', [leadId])
        : db.all('SELECT * FROM replies WHERE deleted_at IS NULL ORDER BY received_at DESC LIMIT 200');
      return send(res, 200, { replies: rows });
    }
    if (p.startsWith('/api/replies/')) {
      const rid = parseInt(decodeURIComponent(p.slice('/api/replies/'.length)), 10);
      const row = db.one('SELECT * FROM replies WHERE id = ?', [rid]);
      if (!row) return send(res, 404, { error: 'unknown reply' });
      if (req.method === 'DELETE') {
        db.run('UPDATE replies SET deleted_at = ? WHERE id = ?', [db.nowISO(), rid]);
        db.logEvent({ entity: 'reply', entity_id: String(rid), type: 'deleted', payload: { lead_id: row.lead_id }, at: db.nowISO(), actor: 'crm' });
        return send(res, 200, { ok: true, id: rid });
      }
      if (req.method === 'PATCH' || req.method === 'PUT') {
        const b = await readBody(req);
        const sets = []; const vals = [];
        for (const c of ['is_read', 'starred']) if (b[c] !== undefined) { sets.push(`${c} = ?`); vals.push(db.bit(b[c])); }
        for (const c of ['classification', 'sentiment']) if (b[c] !== undefined) { sets.push(`${c} = ?`); vals.push(b[c] || null); }
        if (!sets.length) return send(res, 400, { error: 'nothing to update' });
        vals.push(rid);
        db.run(`UPDATE replies SET ${sets.join(', ')} WHERE id = ?`, vals);
        return send(res, 200, { ok: true, reply: db.one('SELECT * FROM replies WHERE id = ?', [rid]) });
      }
    }

    // ---------------- reporting views + raw reads
    if (p === '/api/pipeline' && req.method === 'GET') return send(res, 200, { leads: db.all('SELECT * FROM v_lead_pipeline') });
    if (p === '/api/followups-due' && req.method === 'GET') return send(res, 200, { leads: db.all('SELECT * FROM v_followups_due') });
    if (p === '/api/emails' && req.method === 'GET') {
      const leadId = q.get('lead_id');
      const rows = leadId
        ? db.all('SELECT * FROM emails WHERE lead_id = ? ORDER BY COALESCE(sent_at, created_at) DESC', [leadId])
        : db.all('SELECT * FROM emails ORDER BY COALESCE(sent_at, created_at) DESC LIMIT 200');
      return send(res, 200, { emails: rows });
    }

    // ---------------- sync
    if (p === '/api/sync' && req.method === 'POST') return send(res, 200, await P.syncEmail());

    // ---------------- email passthrough (single sending path: the pad)
    if (p === '/api/email/inbox' && req.method === 'GET') {
      const r = await P.padFetch('GET', '/api/received?limit=' + (q.get('limit') || 50));
      return send(res, r.status, r.json);
    }
    if (p === '/api/email/sent' && req.method === 'GET') {
      const r = await P.padFetch('GET', '/api/sent?limit=' + (q.get('limit') || 50));
      return send(res, r.status, r.json);
    }
    if (p === '/api/email/drafts' && req.method === 'GET') {
      const r = await P.padFetch('GET', '/api/drafts');
      return send(res, r.status, r.json);
    }
    if (p === '/api/email/send' && req.method === 'POST') {
      const b = await readBody(req);
      const r = await P.padFetch('POST', '/api/send', b);
      if (r.status >= 200 && r.status < 300) {
        const lead = db.findLeadByAddress((String(b.to || '').match(db.EMAIL_RE) || [])[0]);
        if (lead) {
          const resendId = (r.json && (r.json.id || (r.json.data && r.json.data.id))) || null;
          const out = P.recordOutbound({ lead, subject: b.subject, to: b.to, text: b.text, html: b.html, resendId, campaign: b.campaign });
          db.logEvent({ entity: 'lead', entity_id: lead.id, type: 'send', payload: { resend_id: resendId, subject: b.subject || '', stage: out.stage }, at: db.nowISO(), actor: 'crm_send' });
        }
      }
      return send(res, r.status, r.json);
    }
    if (p.startsWith('/api/email/drafts/') && p.endsWith('/send') && req.method === 'POST') {
      const draftId = decodeURIComponent(p.slice('/api/email/drafts/'.length, -'/send'.length));
      const b = await readBody(req);
      const r = await P.padFetch('POST', '/api/drafts/' + encodeURIComponent(draftId) + '/send', b);
      if (r.status >= 200 && r.status < 300) {
        const s = db.one('SELECT * FROM drafts WHERE id = ?', [draftId]);
        const lead = (s && s.lead_id ? db.one('SELECT * FROM leads WHERE id = ?', [s.lead_id]) : null)
          || db.findLeadByAddress((String(b.to || (s && s.to_addr) || '').match(db.EMAIL_RE) || [])[0]);
        const resendId = (r.json && (r.json.id || (r.json.data && r.json.data.id))) || (s && s.resend_id) || null;
        if (s) db.run("UPDATE drafts SET status='sent', sent_at=?, resend_id=COALESCE(?, resend_id), updated_at=? WHERE id=?", [db.nowISO(), resendId, db.nowISO(), draftId]);
        if (lead) {
          const out = P.recordOutbound({
            lead, subject: b.subject || (s && s.subject), to: b.to || (s && s.to_addr),
            text: b.text || (s && s.body_text), html: b.html || (s && s.body_html), resendId, campaign: s && s.campaign,
          });
          db.logEvent({ entity: 'lead', entity_id: lead.id, type: 'send', payload: { draft_id: draftId, resend_id: resendId, stage: out.stage }, at: db.nowISO(), actor: 'crm_send' });
        }
      }
      return send(res, r.status, r.json);
    }

    return send(res, 404, { error: 'not found' });
  } catch (e) {
    console.error('[CRM]', (e && e.stack) || e);
    return send(res, 500, { error: String((e && e.message) || e) });
  }
});

server.listen(PORT, HOST, () => {
  console.log(`[${SERVICE}] listening on http://${HOST}:${PORT}  db=${db.DB_PATH}  pad=${process.env.PAD_URL || 'http://127.0.0.1:3001'}`);
  console.log(`[${SERVICE}] stages: ${STAGE_KEYS.join(' -> ')}`);
});
