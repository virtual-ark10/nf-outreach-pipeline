#!/usr/bin/env node
'use strict';
// ============================================================================
//  tools/db-report.cjs — the human view of the SQLite store.
//
//  A .db file does not diff, so this is how you read the state (and what the
//  snapshot commit message summarises).
//
//  Usage: node tools/db-report.cjs [--lead <id>]
// ============================================================================

const db = require('../db.cjs');

const leadArg = (() => {
  const i = process.argv.indexOf('--lead');
  return i >= 0 ? process.argv[i + 1] : null;
})();

console.log(`database: ${db.DB_PATH}`);
console.log('');

const counts = {};
for (const t of ['leads', 'lead_stage_events', 'emails', 'replies', 'drafts', 'events']) {
  counts[t] = db.val(`SELECT COUNT(*) FROM ${t}`);
}
console.log('rows:', JSON.stringify(counts));
console.log('');

if (leadArg) {
  const l = db.one('SELECT * FROM leads WHERE id = ?', [leadArg]);
  if (!l) { console.error('unknown lead: ' + leadArg); process.exit(1); }
  console.log(`lead ${l.id} — ${l.company}`);
  console.log(`  stage=${l.stage} (since ${l.stage_changed_at}) priority=${l.priority} score=${l.score} converted=${l.converted}`);
  console.log(`  email=${l.email} role=${l.contact_role} campaign=${l.campaign}`);
  console.log(`  sponsored_pubs=${l.sponsored_pubs}`);
  console.log(`  first_contact=${l.first_contact_at} last_contact=${l.last_contact_at} next_follow_up=${l.next_follow_up_at}`);
  console.log('  timeline:');
  for (const t of db.all('SELECT kind, at, summary, detail FROM v_lead_timeline WHERE lead_id = ? ORDER BY at', [leadArg])) {
    console.log(`    ${t.at}  ${String(t.kind).padEnd(6)} ${String(t.summary || '').slice(0, 48).padEnd(50)} ${String(t.detail || '').slice(0, 46)}`);
  }
  process.exit(0);
}

console.log('pipeline (v_lead_pipeline):');
for (const r of db.all('SELECT id, stage, converted, emails_sent, replies, last_reply_at, next_follow_up_at FROM v_lead_pipeline ORDER BY stage, id')) {
  console.log(`  ${String(r.id).padEnd(22)} ${String(r.stage).padEnd(12)} conv=${r.converted} sent=${r.emails_sent} replies=${r.replies} due=${r.next_follow_up_at || '-'}`);
}

const due = db.all('SELECT id, stage, days_overdue FROM v_followups_due ORDER BY next_follow_up_at');
console.log('');
console.log(`follow-ups due now: ${due.length}${due.length ? ' -> ' + due.map((d) => `${d.id} (${d.stage}, ${d.days_overdue}d overdue)`).join(', ') : ''}`);

const byStage = db.all('SELECT stage, COUNT(*) n FROM leads WHERE deleted_at IS NULL GROUP BY stage ORDER BY n DESC');
console.log('');
console.log('leads by stage:', byStage.map((r) => `${r.stage}=${r.n}`).join(' '));

const mail = db.all("SELECT direction, status, COUNT(*) n FROM emails GROUP BY direction, status ORDER BY n DESC");
console.log('mail by direction/status:', mail.map((r) => `${r.direction}/${r.status}=${r.n}`).join(' '));

const pending = db.all("SELECT id, company, subject FROM drafts WHERE status = 'draft' ORDER BY created_at");
console.log('');
console.log(`drafts pending: ${pending.length}`);
pending.forEach((d) => console.log(`  ${String(d.id).padEnd(24)} ${String(d.company || '').slice(0, 22).padEnd(24)} ${String(d.subject || '').slice(0, 50)}`));
