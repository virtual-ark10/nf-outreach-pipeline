'use strict';
// ============================================================================
//  pipeline.cjs — the outreach domain layer: the stage machine, what a send does
//  to a lead, what an inbound reply does, and the mail↔CRM sync.
//
//  Both processes use this one file so they can never disagree:
//    server.cjs (the pad, :3001)          — sends, drafts, inbound webhooks
//    leads/server.cjs (the engine, :3002) — the CRM API
//
//  Storage is db.cjs (SQLite); schema in schema.sql. No I/O of its own except the
//  pad HTTP calls the sync needs.
// ============================================================================

const fs = require('fs');
const path = require('path');
const db = require('./db.cjs');

const PAD = process.env.PAD_URL || 'http://127.0.0.1:3001';
// The pad token is what authenticates the sync's calls back into the pad, so the
// leads engine accepts either name (its own deployment may only set one).
const PAD_TOKEN = process.env.PAD_TOKEN || process.env.CRM_TOKEN || '';
const BRAND_DOMAINS = (process.env.PAD_DOMAINS || 'newsletterfit.com')
  .split(',').map((s) => s.trim().toLowerCase()).filter(Boolean);

// ---------------------------------------------------------------- stages
// NF vocabulary: the first touch is "First Email", then the 3/7/14-day cadence.
// terminal: true ends the progression — a send can never drag a lead out of one.
const DEFAULT_STAGES = [
  { key: 'leads',       label: 'Leads',       color: '#64748b', note: 'contact yet to be emailed' },
  { key: 'first_email', label: 'First Email', color: '#2563eb', note: 'first email sent' },
  { key: 'follow_up_1', label: 'Follow-up 1', color: '#0d9488' },
  { key: 'follow_up_2', label: 'Follow-up 2', color: '#4f46e5' },
  { key: 'follow_up_3', label: 'Follow-up 3', color: '#7c3aed' },
  { key: 'follow_up_4', label: 'Follow-up 4', color: '#d97706' },
  { key: 'qualified',   label: 'Qualified',   color: '#0891b2', note: 'interested, shaping a deal' },
  { key: 'replied',     label: 'Replied',     color: '#db2777', terminal: true },
  { key: 'won',         label: 'Won',         color: '#16a34a', terminal: true },
  { key: 'no',          label: 'No',          color: '#dc2626', terminal: true },
  { key: 'archived',    label: 'Archived',    color: '#475569', terminal: true },
];
function readJsonEnv(name) { try { return process.env[name] ? JSON.parse(process.env[name]) : null; } catch (e) { return null; } }
function readConfig() {
  const p = process.env.LEADPAD_CONFIG || path.join(__dirname, 'leads', 'config.json');
  try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch (e) { return {}; }
}
const CFG = readConfig();
const STAGES = readJsonEnv('LEAD_STAGES') || CFG.stages || DEFAULT_STAGES;
const STAGE_KEYS = STAGES.map((s) => s.key);
const PROGRESS = STAGES.filter((s) => !s.terminal && s.key !== STAGES[0].key);
const NEXT_STAGE = {};
PROGRESS.forEach((s, i) => { NEXT_STAGE[s.key] = (PROGRESS[i + 1] || s).key; });
NEXT_STAGE[STAGES[0].key] = (PROGRESS[0] || STAGES[0]).key;
const PROTECTED = Array.from(new Set(STAGES.filter((s) => s.terminal).map((s) => s.key).concat(['replied'])));
const DUE_DAYS = Object.assign(
  { first_email: 3, follow_up_1: 4, follow_up_2: 7, follow_up_3: 0 },
  CFG.dueDays || readJsonEnv('LEAD_DUE_DAYS') || {},
);

function isBrandMail(...vals) {
  const s = vals.flat().filter(Boolean).join(' ').toLowerCase();
  return BRAND_DOMAINS.some((d) => s.includes('@' + d));
}

// ---------------------------------------------------------------- FIELD MAP
// DB column (schema.sql)     -> API field (what the pad UI and the skills read)
//   email                    -> contact_email   (alias `email` also sent)
//   emails                   -> extra_emails
//   priority                 -> quality         (alias `priority` also sent)
//   next_follow_up_at        -> next_action_at  (alias next_follow_up_at also sent)
//   body_text / body_html    -> text / html     (drafts)
//   from_addr / to_addr      -> from / to       (drafts)
const jget = (v, fb) => db.pj(v, fb);
// Lead id from a company name: 'McAlvany Precious Metals' -> 'mcalvany-precious-metals'.
const kebab = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
function rowToLead(r) {
  if (!r) return null;
  const lead = {
    id: r.id, company: r.company, domain: r.domain, website: r.website,
    contact_name: r.contact_name, contact_title: r.contact_title, contact_role: r.contact_role,
    contact_email: r.email, email: r.email,
    extra_emails: jget(r.emails, []), emails: jget(r.emails, []),
    industry: r.industry, city: r.city, region: r.region, country: r.country,
    source: r.source, stage: r.stage, stage_changed_at: r.stage_changed_at,
    quality: r.priority, priority: r.priority, score: r.score, owner: r.owner,
    tags: jget(r.tags, []), notes: jget(r.notes, []), campaign: r.campaign,
    sponsored_pubs: jget(r.sponsored_pubs, []), recommended_pubs: jget(r.recommended_pubs, []),
    angle: r.angle, subscriber_range: r.subscriber_range, meta: jget(r.meta, {}),
    converted: r.converted, converted_at: r.converted_at,
    value_cents: r.value_cents, currency: r.currency,
    unsubscribed: r.unsubscribed, bounced: r.bounced,
    first_contact_at: r.first_contact_at, last_contact_at: r.last_contact_at,
    next_follow_up_at: r.next_follow_up_at, next_action_at: r.next_follow_up_at,
    created_at: r.created_at, updated_at: r.updated_at,
    archived_at: r.archived_at, deleted_at: r.deleted_at,
  };
  return Object.assign(lead, db.derive(r));
}

// The lead's activity stream in the shapes the Leads tab already renders
// (kind: email_out | email_in | stage | <event type>).
function activityFor(id) {
  const acts = [];
  for (const e of db.all("SELECT id, subject, direction, status, sent_at, created_at, stage_at_send FROM emails WHERE lead_id = ? ORDER BY COALESCE(sent_at, created_at)", [id])) {
    acts.push({
      ts: e.sent_at || e.created_at,
      kind: e.direction === 'outbound' ? 'email_out' : 'email_in',
      subject: e.subject,
      detail: (e.direction === 'outbound' ? 'sent' : 'received') + (e.status ? ` [${e.status}]` : '')
        + (e.stage_at_send ? ` while in ${e.stage_at_send}` : ''),
      msg_id: e.id,
    });
  }
  for (const r of db.all('SELECT id, subject, received_at, created_at, from_addr FROM replies WHERE lead_id = ? ORDER BY COALESCE(received_at, created_at)', [id])) {
    acts.push({ ts: r.received_at || r.created_at, kind: 'email_in', subject: r.subject, detail: 'reply from ' + (r.from_addr || 'unknown'), msg_id: 'reply:' + r.id });
  }
  for (const s of db.all('SELECT from_stage, to_stage, at, by, note FROM lead_stage_events WHERE lead_id = ? ORDER BY at', [id])) {
    acts.push({ ts: s.at, kind: 'stage', detail: `${s.from_stage || '(new)'} -> ${s.to_stage}${s.note ? ': ' + s.note : ''}${s.by ? ' (by ' + s.by + ')' : ''}` });
  }
  for (const e of db.all("SELECT type, payload, at FROM events WHERE entity = 'lead' AND entity_id = ? ORDER BY at", [id])) {
    const p = db.pj(e.payload, {});
    acts.push({ ts: e.at, kind: e.type, detail: p.detail || p.body || '', subject: p.subject || '' });
  }
  return acts.sort((a, b) => String(a.ts).localeCompare(String(b.ts))).reverse();
}

// --------------------------------------------------------------- mail records
function nextFollowUp(stage, fromISO) {
  const wait = DUE_DAYS[stage];
  if (!wait) return null;
  return new Date(new Date(fromISO).getTime() + wait * 86400000).toISOString();
}

// A send: one emails row (with the stage frozen at send time) + the stage move.
// This is the only code path that advances a stage on send.
function recordOutbound({ lead, subject, to, text, html, resendId, campaign, status, at, threadId }) {
  const stamp = at || db.nowISO();
  return db.tx(() => {
    const prev = db.one("SELECT id, thread_id FROM emails WHERE lead_id = ? AND direction = 'outbound' ORDER BY id DESC LIMIT 1", [lead.id]);
    const ins = db.run(
      `INSERT OR IGNORE INTO emails
         (lead_id, direction, stage_at_send, thread_id, parent_email_id, to_addr, subject, body_text, body_html,
          campaign, resend_id, status, status_at, sent_at, created_at)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
      [lead.id, 'outbound', lead.stage, threadId || (prev ? prev.thread_id : null), prev ? prev.id : null,
       db.csvList(to), subject || null, text || null, html || null, campaign || lead.campaign || null,
       resendId || null, status || 'sent', stamp, stamp, stamp]
    );
    const id = ins.changes ? Number(ins.lastInsertRowid) : (resendId ? db.val('SELECT id FROM emails WHERE resend_id = ?', [resendId]) : null);
    // Already recorded (the pad and the CRM can both see the same send). Do not
    // advance the stage twice — the unique resend_id makes this idempotent.
    if (!ins.changes) return { email_id: id, stage: lead.stage, advanced: false, duplicate: true };
    if (id && !prev) db.run('UPDATE emails SET thread_id = ? WHERE id = ?', ['thread:' + id, id]);
    db.run(
      `UPDATE leads SET first_contact_at = COALESCE(first_contact_at, ?), last_contact_at = ?,
                        next_follow_up_at = ?, updated_at = ? WHERE id = ?`,
      [stamp, stamp, nextFollowUp(lead.stage, stamp), stamp, lead.id]
    );
    const from = lead.stage;
    const to_stage = PROTECTED.includes(from) ? from : (NEXT_STAGE[from] || from);
    if (to_stage !== from) {
      db.run('UPDATE leads SET stage = ?, stage_changed_at = ?, updated_at = ? WHERE id = ?', [to_stage, stamp, stamp, lead.id]);
      db.stageEvent({ lead_id: lead.id, from_stage: from, to_stage, at: stamp, by: 'send', note: 'auto-advanced by send', source: 'crm_send' });
    }
    return { email_id: id, stage: to_stage, advanced: to_stage !== from, duplicate: !ins.changes };
  });
}

// An inbound message: emails row (direction inbound) + replies row + stage move.
function recordInbound({ from, to, subject, text, html, resendId, messageId, inReplyTo, at, raw, classification, sentiment }) {
  const stamp = at || db.nowISO();
  return db.tx(() => {
    const lead = db.findLeadByAddress(from);
    if (messageId && db.val('SELECT id FROM replies WHERE message_id = ?', [messageId])) {
      return { reply_id: null, duplicate: true, lead_id: lead ? lead.id : null };
    }
    const em = db.run(
      `INSERT OR IGNORE INTO emails (lead_id, direction, to_addr, from_addr, subject, body_text, body_html, resend_id, status, status_at, sent_at, created_at)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
      [lead ? lead.id : null, 'inbound', db.csvList(to), from || null, subject || null, text || null, html || null,
       resendId || null, 'received', stamp, stamp, stamp]
    );
    const emailId = em.changes ? Number(em.lastInsertRowid) : (resendId ? db.val('SELECT id FROM emails WHERE resend_id = ?', [resendId]) : null);
    const r = db.run(
      `INSERT INTO replies (lead_id, email_id, from_addr, to_addr, subject, body_text, body_html, message_id, in_reply_to,
         received_at, classification, sentiment, raw, created_at)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
      [lead ? lead.id : null, emailId, from || null, db.csvList(to), subject || null, text || null, html || null,
       messageId || null, inReplyTo || null, stamp, classification || null, sentiment || null, raw ? db.j(raw) : null, stamp]
    );
    if (lead && !PROTECTED.includes(lead.stage)) {
      db.run('UPDATE leads SET stage = ?, stage_changed_at = ?, last_contact_at = ?, updated_at = ? WHERE id = ?', ['replied', stamp, stamp, stamp, lead.id]);
      db.stageEvent({ lead_id: lead.id, from_stage: lead.stage, to_stage: 'replied', at: stamp, by: 'sync', note: 'reply detected', source: 'inbound' });
    }
    return { reply_id: Number(r.lastInsertRowid), lead_id: lead ? lead.id : null };
  });
}

// A delivery/bounce receipt from Resend: update the message it refers to. A receipt
// with no matching message only gets a row if it belongs to a lead we track.
function recordDeliveryStatus({ resendId, status, to, from, at }) {
  const stamp = at || db.nowISO();
  if (!resendId) return { updated: 0, inserted: 0 };
  return db.tx(() => {
    const r = db.run('UPDATE emails SET status = ?, status_at = ? WHERE resend_id = ?', [status, stamp, resendId]);
    let inserted = 0, bounced = 0;
    if (!r.changes) {
      const lead = db.findLeadByAddress(db.csvList(to)) || db.findLeadByAddress(from);
      if (lead) {
        const ins = db.run(
          `INSERT OR IGNORE INTO emails (lead_id, direction, to_addr, from_addr, resend_id, status, status_at, created_at)
           VALUES (?,?,?,?,?,?,?,?)`,
          [lead.id, 'outbound', db.csvList(to), from || null, resendId, status, stamp, stamp]
        );
        inserted = ins.changes;
      }
    }
    if (status === 'bounced') {
      bounced = db.run('UPDATE leads SET bounced = 1, updated_at = ? WHERE id = (SELECT lead_id FROM emails WHERE resend_id = ?)', [stamp, resendId]).changes;
    }
    return { updated: r.changes, inserted, bounced };
  });
}

// ---------------------------------------------------------------- pad calls
async function padFetch(method, url, body) {
  const opts = { method, headers: { 'X-Pad-Token': PAD_TOKEN } };
  if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const r = await fetch(PAD + url, opts);
  const text = await r.text();
  let json; try { json = JSON.parse(text); } catch (e) { json = { raw: text }; }
  return { status: r.status, json };
}

// --------------------------------------------------------------------- sync
// Reconcile the pad's mail into the CRM: any message the CRM has not recorded yet
// is logged against its lead (which also advances the stage), then stale Leads
// rows are healed. Idempotent — resend_id and replies.message_id are the keys.
async function syncEmail() {
  const result = { logged: 0, matched: 0, unmatched: 0, drafts: 0, healed: 0, checked: { sent: 0, inbox: 0 } };
  for (const [label, url] of [['sent', '/api/sent'], ['inbox', '/api/received']]) {
    let j;
    try { j = (await padFetch('GET', url + '?limit=100')).json; } catch (e) { continue; }
    const list = j.data || j.emails || j.items || (Array.isArray(j) ? j : []);
    result.checked[label] = list.length;
    for (const m of list) {
      const resendId = m.id || m.message_id || m.messageId;
      if (!resendId) continue;
      if (db.val('SELECT id FROM emails WHERE resend_id = ?', [resendId])) continue;
      const addr = label === 'sent' ? (m.to || m.recipient || '') : (m.from || m.sender || '');
      const lead = db.findLeadByAddress(db.csvList(addr));
      if (!lead) { result.unmatched++; continue; }
      if (label === 'sent') {
        recordOutbound({ lead, subject: m.subject || '', to: m.to || addr, resendId, status: 'sent', at: m.created_at || m.sent_at || db.nowISO() });
      } else {
        recordInbound({ from: addr, to: m.received_for || m.to || '', subject: m.subject || '', resendId, messageId: m.message_id || null, at: m.created_at || db.nowISO(), raw: m });
      }
      db.logEvent({ entity: 'lead', entity_id: lead.id, type: 'sync', payload: { side: label, resend_id: resendId, subject: m.subject || '' }, at: db.nowISO(), actor: 'sync' });
      result.logged++; result.matched++;
    }
  }

  // Drafts waiting in the pad, so a lead's stage and its mail agree.
  try {
    const dr = await padFetch('GET', '/api/drafts');
    const drafts = dr.json.data || dr.json.drafts || (Array.isArray(dr.json) ? dr.json : []);
    for (const d of drafts) {
      const lead = db.one('SELECT id FROM leads WHERE id = ?', [d.id]);
      if (!lead) continue;
      const already = db.val("SELECT id FROM drafts WHERE id = ? AND status = 'draft'", [d.id]);
      db.run(
        `INSERT INTO drafts (id, lead_id, company, from_addr, to_addr, cc, reply_to, subject, body_text, body_html, status, created_at, updated_at)
         VALUES (?,?,?,?,?,?,?,?,?,?,'draft',?,?)
         ON CONFLICT(id) DO UPDATE SET status='draft', body_text=excluded.body_text, body_html=excluded.body_html, updated_at=excluded.updated_at`,
        [d.id, lead.id, d.company || null, d.from || null, db.csvList(d.to), db.csvList(d.cc), db.csvList(d.reply_to),
         d.subject || null, d.text || null, d.html || null, d.created_at || db.nowISO(), db.nowISO()]
      );
      if (already) continue;
      db.logEvent({ entity: 'lead', entity_id: lead.id, type: 'note', payload: { detail: 'draft ready in the pad (not sent): ' + (d.subject || '') }, at: db.nowISO(), actor: 'sync' });
      result.drafts++;
    }
  } catch (e) { /* pad drafts unavailable — not fatal */ }

  // Self-heal: outbound mail on record for a lead still parked in 'Leads' means
  // it is past first contact (covers imports and pre-CRM sends).
  for (const lead of db.all('SELECT id FROM leads WHERE stage = ? AND deleted_at IS NULL', [STAGES[0].key])) {
    if (!db.val("SELECT COUNT(*) FROM emails WHERE lead_id = ? AND direction = 'outbound'", [lead.id])) continue;
    const at = db.nowISO();
    db.run('UPDATE leads SET stage = ?, stage_changed_at = ?, updated_at = ? WHERE id = ?', ['first_email', at, at, lead.id]);
    db.stageEvent({ lead_id: lead.id, from_stage: 'leads', to_stage: 'first_email', at, by: 'sync', note: 'outbound mail on record', source: 'sync' });
    result.healed++;
  }
  db.logEvent({ entity: 'system', entity_id: 'email', type: 'sync', payload: result, at: db.nowISO(), actor: 'sync' });
  return result;
}

module.exports = {
  STAGES, STAGE_KEYS, NEXT_STAGE, PROTECTED, DUE_DAYS, PROGRESS, BRAND_DOMAINS,
  isBrandMail, rowToLead, activityFor, nextFollowUp, recordOutbound, recordInbound,
  recordDeliveryStatus, syncEmail, padFetch, kebab,
};
