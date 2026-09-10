#!/usr/bin/env node
'use strict';
// ============================================================================
//  tools/migrate-json.cjs — import the old JSON stores into SQLite, once.
//
//    leads/data/crm.json      -> leads + lead_stage_events + emails + replies + events
//    data/drafts.json         -> drafts
//    data/sent-drafts.jsonl   -> drafts (status=sent) + emails
//    data/webhooks.jsonl      -> emails.status + replies + events
//
//  The JSON files are read, never moved or deleted — they stay as the pre-migration
//  backup. Idempotent-guarded: it refuses to run twice unless --force.
//
//  Usage:
//    node tools/migrate-json.cjs                 # from ./ (pad root) into OUTREACH_DB
//    node tools/migrate-json.cjs --from /path --db /path/outreach.db --force
//
//  Honest gaps, deliberate and reported at the end:
//    * stage_at_send for pre-CRM mail: reconstructed from the recorded stage history
//      when it exists, else taken from the lead's current stage (which was itself
//      derived from that send at import time). Never invented as a transition.
//    * imported "first email" records that explicitly say they carry no send
//      timestamp get sent_at = NULL rather than the import time, so a fake send
//      date never enters the timeline.
// ============================================================================

const fs = require('fs');
const path = require('path');

function arg(name, def) {
  const i = process.argv.indexOf('--' + name);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : def;
}
const FORCE = process.argv.includes('--force');
const FROM = path.resolve(arg('from', path.join(__dirname, '..')));
// Must be set before db.cjs is loaded: it resolves OUTREACH_DB at require time.
if (arg('db', null)) process.env.OUTREACH_DB = path.resolve(arg('db'));
const db = require('../db.cjs');

// Same brand gate as the pad (PAD_DOMAINS): the shared Resend account carries
// other brands' mail and none of it belongs in this CRM.
const BRAND_DOMAINS = (process.env.PAD_DOMAINS || 'newsletterfit.com')
  .split(',').map((s) => s.trim().toLowerCase()).filter(Boolean);

const readJson = (p, def) => { try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch (e) { return def; } };
const readJsonl = (p) => {
  try {
    return fs.readFileSync(p, 'utf8').split('\n').filter(Boolean)
      .map((l) => { try { return JSON.parse(l); } catch (e) { return null; } }).filter(Boolean);
  } catch (e) { return []; }
};

const crm = readJson(path.join(FROM, 'leads/data/crm.json'), { leads: [], activity: [], sync: {} });
const draftsIn = readJson(path.join(FROM, 'data/drafts.json'), []);
const sentIn = readJsonl(path.join(FROM, 'data/sent-drafts.jsonl'));
const hooksIn = readJsonl(path.join(FROM, 'data/webhooks.jsonl'));

const PRIORITY = (q) => { const s = String(q || '').toLowerCase(); return ['high', 'medium', 'low'].includes(s) ? s : null; };
// "carried over from the previous tracker" means exactly that — there is no send
// timestamp, and the entry's ts is the import time. We refuse to promote an import
// time into a send time, so those rows land with sent_at = NULL.
const CARRIED_OVER = /carried over from the previous tracker/i;
const realSend = (a) => Boolean(a.msg_id) && !CARRIED_OVER.test(a.detail || '');

const report = {
  source: { leads: crm.leads.length, activity: crm.activity.length, drafts: draftsIn.length, sent: sentIn.length, webhooks: hooksIn.length },
  insert: {}, skip: {}, notes: [],
};

const existing = db.val('SELECT COUNT(*) FROM leads') || 0;
const migrated = db.val("SELECT COUNT(*) FROM events WHERE type = 'migration'") || 0;
if (migrated && !FORCE) {
  console.error(`This database already records a migration (${db.DB_PATH}). Re-run with --force if you really mean it.`);
  process.exit(1);
}
if (existing && !FORCE) {
  console.error(`leads table already has ${existing} row(s) in ${db.DB_PATH}. Use --force.`);
  process.exit(1);
}

db.tx(() => {
  // ---------------------------------------------------------------- 1. leads
  // Placeholders are derived from the column list so a count mismatch is
  // impossible to ship by hand.
  const LEAD_COLS = [
    'id', 'company', 'domain', 'website', 'contact_name', 'contact_title', 'contact_role',
    'email', 'emails', 'industry', 'city', 'region', 'country', 'source', 'stage',
    'stage_changed_at', 'priority', 'score', 'owner', 'tags', 'notes', 'campaign',
    'sponsored_pubs', 'recommended_pubs', 'angle', 'subscriber_range', 'meta',
    'converted', 'converted_at', 'value_cents', 'currency', 'unsubscribed', 'bounced',
    'first_contact_at', 'last_contact_at', 'next_follow_up_at', 'created_at', 'updated_at',
    'archived_at', 'deleted_at',
  ];
  const LEAD_SQL = `INSERT OR IGNORE INTO leads (${LEAD_COLS.join(', ')})
                    VALUES (${LEAD_COLS.map(() => '?').join(', ')})`;
  const leadRow = (l) => {
    const acts = crm.activity.filter((a) => a.lead_id === l.id);
    const stages = acts.filter((a) => a.kind === 'stage');
    const created = l.created_at || db.nowISO();
    const updated = l.updated_at || created;
    // stage_changed_at: the recorded transition into the lead's current stage,
    // else the last known write. Never invented.
    const lastStageEvent = stages.filter((s) => s.to === l.stage)
      .sort((a, b) => String(a.ts).localeCompare(String(b.ts))).pop();
    const vals = {
      id: l.id,
      company: l.company || l.id,
      domain: l.domain || null,
      website: l.website || null,
      contact_name: l.contact_name || null,
      contact_title: l.contact_title || null,
      contact_role: l.contact_role || null,
      email: l.contact_email || null,
      emails: db.j(l.extra_emails || []),
      industry: l.industry || null,
      city: l.city || null,
      region: l.region || null,
      country: l.country || null,
      source: l.source || 'migrated',
      stage: l.stage || 'leads',
      stage_changed_at: (lastStageEvent && lastStageEvent.ts) || updated,
      priority: PRIORITY(l.quality),
      score: db.int(l.score),
      owner: l.owner || null,
      tags: db.j(l.tags || []),
      notes: db.j(l.notes || []),
      campaign: l.campaign || null,
      sponsored_pubs: db.j(l.sponsored_pubs || []),
      recommended_pubs: db.j(l.recommended_pubs || []),
      angle: l.angle || null,
      subscriber_range: l.subscriber_range || null,
      meta: db.j({
        placements: l.placements ?? null,
        alternates: l.alternates ?? null,
        pick_basis: l.pick_basis ?? null,
        blocked: l.blocked ?? null,
        quality_raw: l.quality ?? null,
        original: l,                       // the untouched source record
      }),
      converted: 0, converted_at: null, value_cents: null, currency: 'USD',
      unsubscribed: 0, bounced: 0,
      first_contact_at: null, last_contact_at: null, next_follow_up_at: null,
      created_at: created, updated_at: updated, archived_at: null, deleted_at: null,
    };
    if (LEAD_COLS.some((c) => !(c in vals))) {
      throw new Error('lead column not populated: ' + LEAD_COLS.filter((c) => !(c in vals)).join(', '));
    }
    return LEAD_COLS.map((c) => vals[c]);
  };

  let inserted = 0, skipped = 0;
  for (const l of crm.leads) {
    const res = db.run(LEAD_SQL, leadRow(l));
    if (res.changes) inserted++; else skipped++;
  }
  report.insert.leads = inserted;
  report.skip.leads = skipped;

  // ------------------------------------------------- 2. activity -> the trails
  let stageEv = 0, noteEv = 0, outEmails = 0, noTs = 0;
  for (const a of crm.activity) {
    const at = a.ts || db.nowISO();
    if (a.kind === 'stage' && a.to) {
      db.run(
        'INSERT INTO lead_stage_events (lead_id, from_stage, to_stage, at, by, note, source) VALUES (?,?,?,?,?,?,?)',
        [a.lead_id, a.from || null, a.to, at, a.source || 'migrated', a.detail || 'migrated from crm.json', a.source || 'json_migration']
      );
      stageEv++;
    } else if (a.kind === 'note') {
      db.logEvent({ entity: 'lead', entity_id: a.lead_id, type: 'note', payload: { detail: a.detail, source: a.source }, at, actor: a.source || 'migrated' });
      noteEv++;
    } else if (a.kind === 'email_out') {
      const lead = db.one('SELECT * FROM leads WHERE id = ?', [a.lead_id]);
      const real = realSend(a);
      if (!real) noTs++;
      db.run(
        `INSERT INTO emails (lead_id, direction, stage_at_send, subject, resend_id, status, status_at, sent_at, created_at)
         VALUES (?,?,?,?,?,?,?,?,?)`,
        [a.lead_id, 'outbound', lead ? lead.stage : null, a.subject || '', a.msg_id || null,
         'sent', at, real ? at : null, at]
      );
      outEmails++;
    } else if (a.kind === 'email_in') {
      const ins = db.run(
        `INSERT INTO emails (lead_id, direction, subject, resend_id, status, status_at, sent_at, created_at)
         VALUES (?,?,?,?,?,?,?,?)`,
        [a.lead_id, 'inbound', a.subject || '', a.msg_id || null, 'received', at, at, at]
      );
      db.run(
        `INSERT INTO replies (lead_id, email_id, subject, message_id, received_at, raw, created_at)
         VALUES (?,?,?,?,?,?,?)`,
        [a.lead_id, Number(ins.lastInsertRowid), a.subject || '', null, at, db.j(a), at]
      );
    } else if (a.kind === 'click') {
      db.logEvent({ entity: 'lead', entity_id: a.lead_id, type: 'click', payload: a, at, actor: a.source || 'migrated' });
    } else {
      db.logEvent({ entity: 'lead', entity_id: a.lead_id, type: a.kind || 'event', payload: a, at, actor: a.source || 'migrated' });
    }
  }
  report.insert.stage_events = stageEv;
  report.insert.notes = noteEv;
  report.insert.emails_from_activity = outEmails;
  if (noTs) report.notes.push(`${noTs} imported 'first email' record(s) had no real send timestamp -> sent_at left NULL`);

  // ---------------------------------------------------------------- 3. drafts
  let dIns = 0;
  for (const d of draftsIn) {
    const lead = db.one('SELECT id FROM leads WHERE id = ?', [d.id]);
    db.run(
      `INSERT OR IGNORE INTO drafts (id, lead_id, company, from_addr, to_addr, cc, reply_to, subject, body_text, body_html,
        status, campaign, created_at, updated_at)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
      [d.id, lead ? lead.id : null, d.company || null, d.from || null, db.csvList(d.to), db.csvList(d.cc),
       db.csvList(d.reply_to), d.subject || null, d.text || null, d.html || null,
       'draft', d.campaign || null, d.created_at || db.nowISO(), d.updated_at || null]
    );
    dIns++;
  }
  report.insert.drafts = dIns;

  // ------------------------------------------------- 4. sent-drafts.jsonl
  // This file is the send-of-record: it carries Resend's id and the true sent_at.
  // So it ENRICHES a row the activity import created without them, rather than
  // being dropped by the unique resend_id — which is what happened to McAlvany's
  // 09-09 send until this step filled in its real timestamp.
  let sentUpd = 0, sentEmails = 0, sentEnriched = 0;
  for (const s of sentIn) {
    const sentAt = s.sent_at || null;
    // the lead this draft belongs to: by draft id, else by the address it went to
    const lead = db.one('SELECT id, stage FROM leads WHERE id = ?', [s.id])
      || db.findLeadByAddress(db.csvList(s.to));
    const existingDraft = db.one('SELECT id FROM drafts WHERE id = ?', [s.id]);
    if (existingDraft) {
      db.run("UPDATE drafts SET status = 'sent', sent_at = ?, resend_id = ?, lead_id = COALESCE(lead_id, ?), updated_at = ? WHERE id = ?",
        [sentAt, s.resend_id || null, lead ? lead.id : null, sentAt || db.nowISO(), s.id]);
      sentUpd++;
    } else {
      db.run(
        `INSERT INTO drafts (id, lead_id, company, to_addr, subject, status, resend_id, created_at, updated_at, sent_at)
         VALUES (?,?,?,?,?,'sent',?,?,?,?)`,
        [s.id, lead ? lead.id : null, s.company || null, db.csvList(s.to), s.subject || null, s.resend_id || null,
         sentAt || db.nowISO(), sentAt || db.nowISO(), sentAt]
      );
      sentUpd++;
    }
    if (s.resend_id) {
      const up = db.run(
        `UPDATE emails
            SET sent_at = COALESCE(?, sent_at),
                stage_at_send = COALESCE(stage_at_send, ?),
                status = 'sent',
                status_at = COALESCE(status_at, ?),
                lead_id = COALESCE(lead_id, ?)
          WHERE resend_id = ?`,
        [sentAt, lead ? lead.stage : null, sentAt, lead ? lead.id : null, s.resend_id]
      );
      if (up.changes) { sentEnriched++; continue; }
    }
    const r = db.run(
      `INSERT OR IGNORE INTO emails (lead_id, direction, stage_at_send, subject, to_addr, resend_id, status, status_at, sent_at, created_at)
       VALUES (?,?,?,?,?,?,?,?,?,?)`,
      [lead ? lead.id : null, 'outbound', lead ? lead.stage : null, s.subject || null, db.csvList(s.to),
       s.resend_id || null, 'sent', sentAt, sentAt, sentAt || db.nowISO()]
    );
    if (r.changes) sentEmails++;
  }
  report.insert.drafts_marked_sent = sentUpd;
  report.insert.emails_from_sentlog = sentEmails;
  report.insert.emails_enriched_from_sentlog = sentEnriched;

  // ------------------------------------------------ 5. webhook archive
  // The Resend account is shared across brands, so the archive is mostly OTHER
  // brands' mail (StarterLens tests, the garage-door outreach, ...). The pad
  // already filters live mail by PAD_DOMAINS; the migration does the same, so the
  // CRM never fills up with another brand's sends. Nothing is lost: every event is
  // still written to `events`, and the raw archive stays in data/webhooks.jsonl.
  // Pass --all-brands to import the whole account anyway.
  const ALL_BRANDS = process.argv.includes('--all-brands');
  const isBrand = (a) => BRAND_DOMAINS.some((dom) => String(a).toLowerCase().includes('@' + dom));
  const hookTypes = {};
  let repliesIns = 0, statusUpd = 0, statusIns = 0, bounced = 0, otherBrand = 0, unattributed = 0;

  for (const rec of hooksIn) {
    const ev = rec.event || {};
    const type = ev.type || 'unknown';
    const at = rec.received_at || ev.created_at || db.nowISO();
    const d = ev.data || {};
    hookTypes[type] = (hookTypes[type] || 0) + 1;
    db.logEvent({ entity: 'webhook', entity_id: d.email_id || d.message_id || null, type, payload: ev, at, actor: 'resend' });

    const addrs = [].concat(d.to || [], d.from || [], d.received_for || []).map(String);
    if (!ALL_BRANDS && !addrs.some(isBrand)) { otherBrand++; continue; }

    if (type === 'email.received') {
      const fromAddr = String(d.from || '');
      const lead = db.findLeadByAddress(fromAddr);
      const toAddr = db.csvList([].concat(d.received_for || [], d.to || []));
      const em = db.run(
        `INSERT OR IGNORE INTO emails (lead_id, direction, subject, from_addr, to_addr, resend_id, status, status_at, sent_at, created_at)
         VALUES (?,?,?,?,?,?,?,?,?,?)`,
        [lead ? lead.id : null, 'inbound', d.subject || null, fromAddr || null, toAddr, d.email_id || null,
         'received', at, d.created_at || at, at]
      );
      const emailId = em.changes ? Number(em.lastInsertRowid)
        : (db.val('SELECT id FROM emails WHERE resend_id = ?', [d.email_id]) || null);
      const dup = d.message_id ? db.val('SELECT id FROM replies WHERE message_id = ?', [d.message_id]) : null;
      if (!dup) {
        db.run(
          `INSERT INTO replies (lead_id, email_id, from_addr, to_addr, subject, message_id, received_at, raw, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)`,
          [lead ? lead.id : null, emailId, fromAddr || null, toAddr, d.subject || null,
           d.message_id || null, d.created_at || at, db.j(rec), at]
        );
        repliesIns++;
      }
    } else if (/^email\.(delivered|bounced|complained|failed|opened|clicked)$/.test(type)) {
      const status = type.split('.')[1];
      const r = db.run('UPDATE emails SET status = ?, status_at = ? WHERE resend_id = ?', [status, at, d.email_id || '']);
      if (r.changes) statusUpd++;
      else {
        // No row for this send. Only give the status a home if the mail belongs to
        // a lead we track — a bare delivery receipt is not a message record.
        const lead = db.findLeadByAddress(db.csvList(d.to)) || db.findLeadByAddress(d.from);
        if (!lead) { unattributed++; continue; }
        const ins = db.run(
          `INSERT OR IGNORE INTO emails (lead_id, direction, to_addr, from_addr, resend_id, status, status_at, created_at)
           VALUES (?,?,?,?,?,?,?,?)`,
          [lead.id, 'outbound', db.csvList(d.to), d.from || null, d.email_id || null, status, at, at]
        );
        if (ins.changes) statusIns++;
      }
      if (status === 'bounced') {
        const b = db.run('UPDATE leads SET bounced = 1, updated_at = ? WHERE id = (SELECT lead_id FROM emails WHERE resend_id = ?)', [at, d.email_id || '']);
        if (b.changes) bounced++;
      }
    }
  }
  report.insert.replies = repliesIns;
  report.insert.email_status_updates = statusUpd + statusIns;
  report.webhook_types = hookTypes;
  report.webhook_other_brand_skipped = otherBrand;
  report.webhook_unattributed_skipped = unattributed;
  if (otherBrand) report.notes.push(`${otherBrand} webhook event(s) belonged to another brand on the shared Resend account -> logged to events only, not imported as CRM mail`);
  if (unattributed) report.notes.push(`${unattributed} delivery/bounce event(s) matched no lead -> logged to events only`);

  // ------------------------------------ 6. contact stamps + follow-up clock
  const stamp = db.run(
    `UPDATE leads SET
       first_contact_at = (SELECT MIN(sent_at) FROM emails e WHERE e.lead_id = leads.id AND e.direction = 'outbound' AND e.sent_at IS NOT NULL),
       last_contact_at  = (SELECT MAX(sent_at) FROM emails e WHERE e.lead_id = leads.id AND e.direction = 'outbound' AND e.sent_at IS NOT NULL)`
  );
  const due = db.run(
    `UPDATE leads SET next_follow_up_at = (
       SELECT strftime('%Y-%m-%dT%H:%M:%fZ', datetime(e.sent_at, '+' || ? || ' days'))
       FROM emails e WHERE e.lead_id = leads.id AND e.direction = 'outbound' AND e.sent_at IS NOT NULL
       ORDER BY e.sent_at DESC LIMIT 1)
     WHERE stage IN (SELECT key FROM (SELECT 'first_email' key, ? d UNION ALL SELECT 'follow_up_1', ? UNION ALL SELECT 'follow_up_2', ?))
       AND (SELECT COUNT(*) FROM emails e WHERE e.lead_id = leads.id AND e.direction='outbound' AND e.sent_at IS NOT NULL) > 0`,
    [db.DUE_DAYS.first_email || 3, db.DUE_DAYS.first_email || 3, db.DUE_DAYS.follow_up_1 || 4, db.DUE_DAYS.follow_up_2 || 7]
  );
  report.touched = { leads_stamped: stamp.changes, followups_scheduled: due.changes };
  report.notes.push('next_follow_up_at was backfilled for active leads from the cadence in LEAD_DUE_DAYS');

  db.logEvent({
    entity: 'system', entity_id: 'json_migration', type: 'migration',
    payload: { from: FROM, at: db.nowISO(), source_counts: report.source, notes: report.notes },
    at: db.nowISO(), actor: 'tools/migrate-json.cjs',
  });

  if (crm.sync && crm.sync.email) {
    db.logEvent({ entity: 'system', entity_id: 'sync', type: 'sync', payload: crm.sync, at: crm.sync.email, actor: 'crm_json' });
  }
});

// ------------------------------------------------------------------ report
const counts = {};
for (const t of ['leads', 'lead_stage_events', 'emails', 'replies', 'drafts', 'events']) {
  counts[t] = db.val(`SELECT COUNT(*) FROM ${t}`);
}
console.log('migration -> ' + db.DB_PATH);
console.log('source:', JSON.stringify(report.source));
console.log('inserted:', JSON.stringify(report.insert));
console.log('webhook event types:', JSON.stringify(report.webhook_types || {}));
console.log('touched:', JSON.stringify(report.touched || {}));
report.notes.forEach((n) => console.log('note:', n));
console.log('table counts:', JSON.stringify(counts));
console.log('pipeline view:');
db.all('SELECT id, stage, emails_sent, replies, next_follow_up_at FROM v_lead_pipeline ORDER BY stage, id').forEach((r) => {
  console.log(`   ${String(r.id).padEnd(22)} ${String(r.stage).padEnd(12)} sent=${r.emails_sent} replies=${r.replies} due=${r.next_follow_up_at || '-'}`);
});
