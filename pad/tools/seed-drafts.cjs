#!/usr/bin/env node
'use strict';
// ============================================================================
//  tools/seed-drafts.cjs — put drafts into the pad's review queue.
//
//  The queue is rows in the SQLite store (table `drafts`, status='draft').
//  Before the cutover a batch was seeded by appending objects to
//  data/drafts.json; that file is now a retired backup, so appending to it
//  silently does nothing. This is the supported replacement.
//
//  Input: a JSON array of the same draft objects the pad's API speaks —
//         {id, company, to, cc, subject, from, reply_to, text, html, campaign}
//         to/cc may be a string ("a@x.com, b@y.com") or an array.
//         `id` defaults to the kebab of the company.
//
//  Idempotent by id: an existing draft is UPDATED in place (so re-running picks
//  up body edits), never duplicated. A draft the operator already SENT or
//  DISCARDED is left alone — reseeding must not resurrect it into the queue.
//
//  Usage:
//    node tools/seed-drafts.cjs --file batch.json
//    node tools/seed-drafts.cjs --file batch.json --dry-run
//    node tools/seed-drafts.cjs --file batch.json --db <path>   # aim at a copy
// ============================================================================

const fs = require('fs');
const path = require('path');

function arg(name, def) {
  const i = process.argv.indexOf('--' + name);
  const v = i >= 0 ? process.argv[i + 1] : null;
  return v && !v.startsWith('--') ? v : def;
}
const DRY = process.argv.includes('--dry-run');
if (arg('db', null)) process.env.OUTREACH_DB = path.resolve(arg('db'));

const db = require('../db.cjs');
const P = require('../pipeline.cjs');

const FILE = arg('file', null);
if (!FILE) { console.error('usage: node tools/seed-drafts.cjs --file <drafts.json> [--dry-run]'); process.exit(1); }

const list = JSON.parse(fs.readFileSync(path.resolve(FILE), 'utf8'));
if (!Array.isArray(list)) { console.error('expected a JSON array of draft objects'); process.exit(1); }

const rows = [];
for (const d of list) {
  const to = db.csvList(d.to);
  if (!to || !d.subject) { console.error('skipping a draft with no to/subject:', d.id || d.company); continue; }
  const id = d.id || P.kebab(d.company || to);
  const lead = db.findLeadByAddress((String(to).match(db.EMAIL_RE) || [])[0])
    || db.one('SELECT id FROM leads WHERE id = ?', [id]);
  rows.push({
    id,
    lead_id: lead ? lead.id : null,
    company: d.company || null,
    from_addr: d.from || process.env.FROM_EMAIL || null,
    to_addr: to,
    cc: db.csvList(d.cc),
    reply_to: db.csvList(d.reply_to),
    subject: d.subject,
    body_text: d.text || null,
    body_html: d.html || null,
    campaign: d.campaign || null,
  });
}

let created = 0, updated = 0, skipped = 0;
const report = [];

function DryRun() {}
try {
  db.tx(() => {
    for (const r of rows) {
    const existing = db.one('SELECT id, status FROM drafts WHERE id = ?', [r.id]);
    if (existing && existing.status !== 'draft') {
      // sent or discarded — do not drag it back into the queue
      skipped++;
      report.push(`  [SKIP]  ${r.id} (status=${existing.status})`);
      continue;
    }
    const now = db.nowISO();
    if (existing) {
      db.run(
        `UPDATE drafts SET lead_id = COALESCE(?, lead_id), company = ?, from_addr = ?, to_addr = ?,
           cc = ?, reply_to = ?, subject = ?, body_text = ?, body_html = ?,
           campaign = COALESCE(?, campaign), updated_at = ?
         WHERE id = ?`,
        [r.lead_id, r.company, r.from_addr, r.to_addr, r.cc, r.reply_to,
         r.subject, r.body_text, r.body_html, r.campaign, now, r.id]
      );
      updated++;
      report.push(`  [UPDATE] ${r.id} -> ${r.to_addr}`);
    } else {
      db.run(
        `INSERT INTO drafts (id, lead_id, company, from_addr, to_addr, cc, reply_to, subject,
           body_text, body_html, status, campaign, created_at, updated_at)
         VALUES (?,?,?,?,?,?,?,?,?,?,'draft',?,?,?)`,
        [r.id, r.lead_id, r.company, r.from_addr, r.to_addr, r.cc, r.reply_to, r.subject,
         r.body_text, r.body_html, r.campaign, now, now]
      );
      created++;
      report.push(`  [NEW]    ${r.id} -> ${r.to_addr}`);
    }
  }
    if (DRY) throw new DryRun();
  });
} catch (e) {
  if (!(e instanceof DryRun)) throw e;
}

report.forEach((l) => console.log(l));
console.log(`\nseed -> ${db.DB_PATH}   queue: ${db.val("SELECT COUNT(*) FROM drafts WHERE status = 'draft'")} pending` +
  (DRY ? '   [DRY RUN — nothing written]' : ''));
console.log(`  new: ${created}   updated: ${updated}   left alone (sent/discarded): ${skipped}`);
