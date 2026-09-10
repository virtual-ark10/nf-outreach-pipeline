#!/usr/bin/env node
'use strict';
// ============================================================================
//  tools/test-sqlite-e2e.cjs — end-to-end check of the SQLite rewrite.
//
//  Drives the two REAL processes (pad :3997, CRM :3998 by default) over HTTP
//  against a throwaway database, plus one direct call into the domain layer for
//  the send path (which needs a Resend key we do not want to spend in a test).
//
//  Usage: node tools/test-sqlite-e2e.cjs
// ============================================================================

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const PAD = process.env.TEST_PAD || 'http://127.0.0.1:3997';
const CRM = process.env.TEST_CRM || 'http://127.0.0.1:3998';
const TOKEN = process.env.TEST_TOKEN || 'testtoken';

process.env.OUTREACH_DB = process.env.OUTREACH_DB || '/tmp/e2e.db';

let pass = 0, fail = 0;
const results = [];
function check(name, cond, extra) {
  if (cond) { pass++; results.push('  PASS  ' + name); }
  else { fail++; results.push('  FAIL  ' + name + (extra !== undefined ? '  -> ' + JSON.stringify(extra) : '')); }
}
async function api(base, p, opts = {}) {
  const res = await fetch(base + p, Object.assign({
    headers: Object.assign({ 'X-Pad-Token': TOKEN }, base === CRM ? { 'X-CRM-Token': TOKEN } : {}),
  }, opts));
  let body = null;
  try { body = await res.json(); } catch (e) { body = null; }
  return { status: res.status, body };
}
function sign(secret, id, ts, body) {
  const key = Buffer.from(secret.startsWith('whsec_') ? secret.slice(6) : secret, 'base64');
  return crypto.createHmac('sha256', key).update(`${id}.${ts}.${body}`).digest('base64');
}

(async () => {
  // ---------------------------------------------------------------- health
  const h1 = await api(PAD, '/api/health');
  check('pad /api/health 200', h1.status === 200, h1);
  const h2 = await api(CRM, '/api/health');
  check('crm /api/health 200 + sqlite file', h2.status === 200 && h2.body && h2.body.db === 'e2e.db', h2.body);
  const noauth = await fetch(CRM + '/api/leads');
  check('crm rejects a missing token (401)', noauth.status === 401, noauth.status);

  // ---------------------------------------------------------------- meta
  const meta = await api(CRM, '/api/meta');
  check('meta lists the 11 NF stages', meta.body && meta.body.stages.length === 11, meta.body && meta.body.stages.map((s) => s.key));
  check('meta reports the sqlite engine', meta.body && meta.body.storage && /sqlite/.test(meta.body.storage.engine), meta.body && meta.body.storage);

  // ------------------------------------------------- events: the failure backbone
  // A rejected request must leave a countable failure event behind, not just a log
  // line. The 401 above (crm rejects a missing token) is the trigger.
  const tr = await api(PAD, '/api/tracking?days=30');
  check('GET /api/tracking answers with the dashboard aggregates',
    tr.status === 200 && tr.body && tr.body.totals && Array.isArray(tr.body.by_day)
    && Array.isArray(tr.body.statuses) && Array.isArray(tr.body.leads) && Array.isArray(tr.body.recent_errors),
    tr.body && Object.keys(tr.body));
  check('tracking totals carry the engagement numbers',
    tr.body && ['sent', 'delivered', 'bounced', 'replies', 'clicks', 'opens', 'errors'].every((k) => typeof tr.body.totals[k] === 'number'),
    tr.body && tr.body.totals);
  check('tracking names its sources honestly',
    tr.body && tr.body.sources && tr.body.sources.opens_tracked === false && typeof tr.body.sources.store === 'string',
    tr.body && tr.body.sources);

  // the CRM's rejected token must be visible in the same events table the
  // dashboard reads — failure -> events row -> dashboard, end to end
  check('the rejected token reaches the dashboard failure list',
    (tr.body.recent_errors || []).some((e) => e.op === 'auth'),
    tr.body && tr.body.recent_errors);

  // and the same for the pad's own auth gate
  const raw401 = await fetch(PAD + '/api/tracking', { headers: { 'X-Pad-Token': 'not-the-token' } });
  check('pad rejects a bad token (401)', raw401.status === 401, raw401.status);
  const tr2 = await api(PAD, '/api/tracking?days=30');
  check('the pad records its own rejection as a failure event',
    (tr2.body.recent_errors || []).some((e) => e.op === 'auth' && e.actor === 'pad'),
    tr2.body && tr2.body.recent_errors);
  check('meta counts add up to the migrated total',
    meta.body && Object.values(meta.body.counts).reduce((a, b) => a + b, 0) === meta.body.total, meta.body && meta.body.counts);

  // ---------------------------------------------------------------- leads
  const leads = await api(CRM, '/api/leads');
  check('16 migrated leads come back', leads.body && leads.body.leads.length === 16, leads.body && leads.body.leads.length);
  const m = leads.body.leads.find((l) => l.id === 'mcalvany');
  check('lead carries the legacy API field names',
    m && m.contact_email === 'robert@mcalvany.com' && Array.isArray(m.sponsored_pubs), m && { email: m.contact_email, pubs: m.sponsored_pubs });
  check('decorated counts present (emails_sent)', m && m.emails_sent === 1, m && m.emails_sent);
  check('priority mapped from the old quality field', m && m.priority === 'medium' && m.quality === 'medium', m && { p: m.priority, q: m.quality });

  const detail = await api(CRM, '/api/leads/mcalvany');
  check('lead detail returns a rendered activity stream',
    detail.body && Array.isArray(detail.body.activity) && detail.body.activity.some((a) => a.kind === 'email_out'), detail.body && detail.body.activity);

  const tl = await api(CRM, '/api/leads/mcalvany/timeline');
  check('v_lead_timeline returns email + stage rows',
    tl.body && tl.body.timeline.filter((r) => r.kind === 'email').length === 1
    && tl.body.timeline.filter((r) => r.kind === 'stage').length === 2, tl.body && tl.body.timeline);

  const pipe = await api(CRM, '/api/pipeline');
  check('v_lead_pipeline has a row per live lead', pipe.body && pipe.body.leads.length === 16, pipe.body && pipe.body.leads.length);
  const due = await api(CRM, '/api/followups-due');
  check('v_followups_due answers (0 or more rows)', due.status === 200 && Array.isArray(due.body.leads), due.body);

  // ---------------------------------------------------------------- create
  await api(CRM, '/api/leads/acme-test', { method: 'DELETE' });
  const created = await api(CRM, '/api/leads', {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CRM-Token': TOKEN },
    body: JSON.stringify({ id: 'acme-test', company: 'Acme Test Co', domain: 'acme.test', contact_email: 'dana@acme.test', contact_role: 'gtm', source: 'test', campaign: 'e2e' }),
  });
  check('POST /api/leads creates (201)', created.status === 201, created);
  check('new lead starts in "leads" with a birth stage event',
    created.body && created.body.lead.stage === 'leads' && created.body.lead.stage_changed_at, created.body && created.body.lead);
  const dupe = await api(CRM, '/api/leads', {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CRM-Token': TOKEN },
    body: JSON.stringify({ id: 'acme-test', company: 'Acme Test Co' }),
  });
  check('duplicate create is a 409', dupe.status === 409, dupe.status);
  // an id is derived from the company name when none is supplied
  const derived = await api(CRM, '/api/leads', {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CRM-Token': TOKEN },
    body: JSON.stringify({ company: 'Kebab Case Co' }),
  });
  check('id derives from the company name when omitted',
    derived.status === 201 && derived.body.lead.id === 'kebab-case-co', derived.body && derived.body.lead && derived.body.lead.id);

  // ---------------------------------------------------------------- stage moves
  const bad = await api(CRM, '/api/leads/acme-test', {
    method: 'PATCH', headers: { 'Content-Type': 'application/json', 'X-CRM-Token': TOKEN },
    body: JSON.stringify({ stage: 'nonsense' }),
  });
  check('bad stage rejected (400)', bad.status === 400, bad.body);
  const moved = await api(CRM, '/api/leads/acme-test', {
    method: 'PATCH', headers: { 'Content-Type': 'application/json', 'X-CRM-Token': TOKEN },
    body: JSON.stringify({ stage: 'qualified', priority: 'high', value_cents: 250000, converted: true, note: 'e2e move' }),
  });
  check('PATCH moves the stage and writes the field map',
    moved.status === 200 && moved.body.lead.stage === 'qualified' && moved.body.lead.priority === 'high'
    && moved.body.lead.value_cents === 250000 && moved.body.lead.converted === 1, moved.body && moved.body.lead);
  check('converted is separate from stage', moved.body && moved.body.lead.stage !== 'won' && moved.body.lead.converted === 1);
  check('converted_at stamped automatically', moved.body && !!moved.body.lead.converted_at);
  const t2 = await api(CRM, '/api/leads/acme-test/timeline');
  check('stage history has both transitions',
    t2.body && t2.body.timeline.filter((r) => r.kind === 'stage').length === 2, t2.body && t2.body.timeline);

  const note = await api(CRM, '/api/leads/acme-test/note', {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CRM-Token': TOKEN },
    body: JSON.stringify({ body: 'note from the e2e test' }),
  });
  check('POST note accepted (201)', note.status === 201, note.body);

  // ---------------------------------------------------------------- the send path
  // Direct call into the domain layer: a real send needs a Resend key, and the
  // point here is the stage machine + the frozen stage_at_send, not Resend.
  const db = require('../db.cjs');
  const P = require('../pipeline.cjs');
  const lead = db.one('SELECT * FROM leads WHERE id = ?', ['acme-test']);
  const out1 = P.recordOutbound({ lead, subject: 'e2e first email', to: ['dana@acme.test'], text: 'hi', resendId: 'e2e-resend-1' });
  check('send advances qualified -> (next progress stage)', out1.stage === 'qualified', out1);
  const l2 = db.one('SELECT stage, first_contact_at, last_contact_at, next_follow_up_at FROM leads WHERE id = ?', ['acme-test']);
  check('first/last contact stamped on send', !!l2.first_contact_at && !!l2.last_contact_at && l2.first_contact_at === l2.last_contact_at, l2);
  const e1 = db.one('SELECT * FROM emails WHERE resend_id = ?', ['e2e-resend-1']);
  check('emails row written with direction + status', e1 && e1.direction === 'outbound' && e1.status === 'sent', e1);
  check('thread_id roots the thread', e1 && e1.thread_id === 'thread:' + e1.id, e1 && e1.thread_id);
  check('body stored', e1 && e1.body_text === 'hi', e1 && e1.body_text);

  // stage_at_send must be frozen: move the lead, then re-read the old row.
  const leadRow2 = db.one('SELECT * FROM leads WHERE id = ?', ['acme-test']);
  P.recordOutbound({ lead: leadRow2, subject: 'e2e follow-up', to: ['dana@acme.test'], text: 'bump', resendId: 'e2e-resend-2' });
  db.run("UPDATE leads SET stage = 'won' WHERE id = ?", ['acme-test']);
  const e1again = db.one('SELECT stage_at_send FROM emails WHERE resend_id = ?', ['e2e-resend-1']);
  check('stage_at_send does not rewrite itself when the lead moves', e1again.stage_at_send === 'qualified', e1again);
  const dup = P.recordOutbound({ lead: leadRow2, subject: 'dup', to: ['dana@acme.test'], text: 'dup', resendId: 'e2e-resend-1' });
  check('a repeated resend_id is a no-op, not a second stage move', dup.duplicate === true && dup.advanced === false, dup);

  // protected stages end the progression
  const wonLead = db.one('SELECT * FROM leads WHERE id = ?', ['acme-test']);
  const out3 = P.recordOutbound({ lead: wonLead, subject: 'should not advance', to: ['dana@acme.test'], resendId: 'e2e-resend-3' });
  check('a send never drags a lead out of a terminal stage', out3.advanced === false && out3.stage === 'won', out3);

  // ---------------------------------------------------------------- inbound + replies
  const inb = P.recordInbound({ from: 'dana@acme.test', to: 'ian@newsletterfit.com', subject: 'Re: e2e', text: 'interested', messageId: '<e2e-msg-1@acme.test>', resendId: 'e2e-in-1', at: new Date().toISOString(), raw: { test: true } });
  check('inbound reply recorded and linked to the lead', inb.reply_id && inb.lead_id === 'acme-test', inb);
  const again = P.recordInbound({ from: 'dana@acme.test', to: 'ian@newsletterfit.com', subject: 'Re: e2e', messageId: '<e2e-msg-1@acme.test>', resendId: 'e2e-in-2' });
  check('duplicate inbound message_id is rejected', again.duplicate === true, again);
  const wonNow = db.one('SELECT stage FROM leads WHERE id = ?', ['acme-test']);
  check('terminal lead is not moved to replied', wonNow.stage === 'won', wonNow);

  const rep = await api(CRM, '/api/replies?lead_id=acme-test');
  check('GET /api/replies?lead_id=',
    rep.body && rep.body.replies.length === 1 && rep.body.replies[0].message_id === '<e2e-msg-1@acme.test>', rep.body);
  const mark = await api(CRM, '/api/replies/' + rep.body.replies[0].id, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json', 'X-CRM-Token': TOKEN },
    body: JSON.stringify({ is_read: true, starred: true, classification: 'interested', sentiment: 'positive' }),
  });
  check('PATCH reply flags', mark.body && mark.body.reply.is_read === 1 && mark.body.reply.starred === 1 && mark.body.reply.classification === 'interested', mark.body);
  const del = await api(CRM, '/api/replies/' + rep.body.replies[0].id, { method: 'DELETE' });
  check('DELETE reply soft-deletes', del.status === 200 && del.body.ok === true, del.body);
  const after = await api(CRM, '/api/replies?lead_id=acme-test');
  check('soft-deleted reply disappears from the inbox view', after.body && after.body.replies.length === 0, after.body);
  const raw = db.one('SELECT deleted_at, raw, body_text FROM replies WHERE message_id = ?', ['<e2e-msg-1@acme.test>']);
  check('the row survives the ✕ with its deleted_at stamp', raw && !!raw.deleted_at, raw && Object.keys(raw));
  check('the raw payload column is populated', raw && raw.raw !== null, raw && raw.raw);

  // ---------------------------------------------------------------- drafts (pad)
  const d0 = await api(PAD, '/api/drafts');
  check('pad /api/drafts returns the 7 migrated drafts', d0.body && d0.body.data.length === 7, d0.body && d0.body.data.length);
  check('draft keeps the legacy API shape', d0.body && 'to' in d0.body.data[0] && 'text' in d0.body.data[0] && 'from' in d0.body.data[0], d0.body && Object.keys(d0.body.data[0]));
  const target = d0.body.data[0].id;
  const put = await api(PAD, '/api/drafts/' + target, {
    method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-Pad-Token': TOKEN },
    body: JSON.stringify({ subject: 'e2e edited subject' }),
  });
  check('PUT edits a draft in place', put.status === 200, put.body);
  const d1 = await api(PAD, '/api/drafts');
  check('the edit is persisted',
    (d1.body.data.find((x) => x.id === target) || {}).subject === 'e2e edited subject', d1.body.data.find((x) => x.id === target));
  const disc = await api(PAD, '/api/drafts/' + target, { method: 'DELETE', headers: { 'X-Pad-Token': TOKEN } });
  check('DELETE discards (status, not row loss)', disc.status === 200 && disc.body.status === 'discarded', disc.body);
  const d2 = await api(PAD, '/api/drafts');
  check('discarded draft leaves the queue', d2.body.data.length === 6, d2.body.data.length);
  const stillThere = db.one('SELECT status, discarded_at, subject FROM drafts WHERE id = ?', [target]);
  check('discarded draft row survives with its history', stillThere && stillThere.status === 'discarded' && !!stillThere.discarded_at, stillThere);

  // ---------------------------------------------------------------- webhook (signed)
  const envTxt = fs.readFileSync(path.join(__dirname, '..', '.env'), 'utf8');
  const secret = (envTxt.match(/^RESEND_WEBHOOK_SECRET=(.*)$/m) || [])[1];
  if (!secret) {
    results.push('  SKIP  webhook test (no RESEND_WEBHOOK_SECRET in .env)');
  } else {
    const ev = {
      type: 'email.received', created_at: new Date().toISOString(),
      data: {
        email_id: 'e2e-hook-1', message_id: '<e2e-hook-1@acme.test>', from: 'dana@acme.test',
        to: ['ian@newsletterfit.com'], received_for: ['ian@newsletterfit.com'], subject: 'hook reply', attachments: [],
      },
    };
    const raw = JSON.stringify(ev);
    const id = 'msg_e2e_' + Date.now();
    const ts = String(Math.floor(Date.now() / 1000));
    const sig = sign(secret, id, ts, raw);
    const hookRes = await fetch(PAD + '/api/webhook', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'svix-id': id, 'svix-timestamp': ts, 'svix-signature': 'v1,' + sig },
      body: raw,
    });
    const hookBody = await hookRes.json();
    check('signed webhook accepted (200)', hookRes.status === 200 && hookBody.ok === true, hookBody);
    const hr = db.one('SELECT * FROM replies WHERE message_id = ?', ['<e2e-hook-1@acme.test>']);
    check('webhook wrote a replies row with the raw payload', hr && hr.raw && /e2e-hook-1/.test(hr.raw), hr && Object.keys(hr));
    const badRes = await fetch(PAD + '/api/webhook', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'svix-id': id, 'svix-timestamp': ts, 'svix-signature': 'v1,notarealsignature' },
      body: raw,
    });
    check('a bad signature is rejected (400)', badRes.status === 400, badRes.status);
    const other = {
      type: 'email.delivered', created_at: new Date().toISOString(),
      data: { email_id: 'e2e-hook-other', to: ['someone@otherbrand.com'], from: 'x@otherbrand.com' },
    };
    const rawOther = JSON.stringify(other);
    const id2 = 'msg_e2e_other';
    const sig2 = sign(secret, id2, ts, rawOther);
    const otherRes = await fetch(PAD + '/api/webhook', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'svix-id': id2, 'svix-timestamp': ts, 'svix-signature': 'v1,' + sig2 },
      body: rawOther,
    });
    const otherBody = await otherRes.json();
    check('another brand\'s event is logged but not imported as CRM mail',
      otherRes.status === 200 && otherBody.skipped && !db.val('SELECT id FROM emails WHERE resend_id = ?', ['e2e-hook-other']), otherBody);
  }
  const arch = await api(PAD, '/api/archive?limit=5', { headers: { 'X-Pad-Token': TOKEN } });
  check('/api/archive reads back from the events table', arch.status === 200 && Array.isArray(arch.body.data) && arch.body.data.length > 0, arch.body && arch.body.data.length);

  // ---------------------------------------------------------------- sync
  const prox = await api(CRM, '/api/email/drafts');
  check('the CRM can authenticate back to the pad (token handshake)', prox.status === 200, prox);
  const sync = await api(CRM, '/api/sync', { method: 'POST' });
  check('POST /api/sync completes', sync.status === 200 && sync.body && 'logged' in sync.body, sync.body);
  check('sync reported what it checked', sync.body && sync.body.checked && 'sent' in sync.body.checked && 'inbox' in sync.body.checked, sync.body && sync.body.checked);

  // ---------------------------------------------------------------- report
  console.log(results.join('\n'));
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch((e) => {
  console.error('test harness error:', e && e.message);
  console.log(results.join('\n'));
  console.log(`\n${pass} passed, ${fail} failed (aborted early)`);
  process.exit(2);
});
