#!/usr/bin/env node
'use strict';
// ============================================================================
//  tools/import-tracker.cjs — backfill historical tracker rows into the CRM db.
//
//  This replaces leads/import_tracker.py, which wrote the retired
//  leads/data/crm.json (a file nothing reads any more since the SQLite cutover).
//  Same job, same CSV, but the rows land in the real store through the shared
//  storage + domain layer.
//
//    * a company not in the db       -> created as a LEAD, source 'import'
//    * a company already there       -> only MISSING fields are filled, never a
//                                       clobber of an edit made in the CRM
//    * a contact row with no company -> appended to the matching lead's extra
//                                       emails (matched on the lead's domain)
//    * the tracker's "First Email" / "Follow-up 1" columns -> rows in `drafts`
//      with status 'draft'. They hold an unsent draft body, and the export
//      carries no send timestamp, so they are NOT recorded as sent mail and the
//      lead is NOT advanced: a company whose first email is still only a draft
//      is a Lead, not "First Email" (the same rule the rest of the pipeline uses).
//
//  Idempotent: leads match on the kebab id, drafts on their derived id, so
//  re-running fills gaps and never duplicates or overwrites an edited draft.
//
//  Usage:
//    node tools/import-tracker.cjs                  # /home/boxed/backfill_rows.csv
//    node tools/import-tracker.cjs --csv <file>
//    node tools/import-tracker.cjs --dry-run        # report only, write nothing
//    node tools/import-tracker.cjs --db <path>      # aim at a copy, not the live db
//
//  The live database is the default target. It prints the path it writes to
//  before writing anything; use --dry-run first if you want to see the diff.
// ============================================================================

const fs = require('fs');
const path = require('path');

function arg(name, def) {
  const i = process.argv.indexOf('--' + name);
  const v = i >= 0 ? process.argv[i + 1] : null;
  return v && !v.startsWith('--') ? v : def;
}
const DRY = process.argv.includes('--dry-run');
// Must be set before db.cjs is required: it resolves OUTREACH_DB at load time.
if (arg('db', null)) process.env.OUTREACH_DB = path.resolve(arg('db'));

const db = require('../db.cjs');
const P = require('../pipeline.cjs');

const CSV = path.resolve(arg('csv', '/home/boxed/backfill_rows.csv'));
const LOCAL_PART = /^(info|hello|contact|team|sales|admin|support|hi)$/i;
const ADDR = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/;

// ---------------------------------------------------------------- csv
// RFC4180 enough for this export: the draft bodies contain commas, quotes AND
// newlines, so a split on ',' or '\n' would shred the file.
function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = '';
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; } else { inQuotes = false; }
      } else { field += c; }
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === ',') {
      row.push(field); field = '';
    } else if (c === '\r') {
      // ignore
    } else if (c === '\n') {
      row.push(field); rows.push(row); row = []; field = '';
    } else {
      field += c;
    }
  }
  if (field !== '' || row.length) { row.push(field); rows.push(row); }
  return rows.filter((r) => r.some((c) => String(c).trim() !== ''));
}

// ---------------------------------------------------------------- helpers
function roleOf(title) {
  const t = String(title || '').toLowerCase();
  if (t.includes('go-to-market') || /\bgtm\b/.test(t)) return 'gtm';
  if (t.includes('growth')) return 'growth';
  if (t.includes('partnership') || t.includes('business development')) return 'partnerships';
  if (t.includes('marketing')) return 'marketing';
  if (/founder|ceo|coo|owner|chief executive/.test(t)) return 'founder';
  return 'other';
}

function nameFromEmail(email) {
  const local = String(email || '').split('@')[0] || '';
  if (!local || LOCAL_PART.test(local)) return '';
  return local.split(/[._-]+/).filter(Boolean)
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1)).join(' ');
}

// ---------------------------------------------------------------- run
const text = fs.readFileSync(CSV, 'utf8');
const rows = parseCsv(text);
if (!rows.length) { console.error('no rows found in ' + CSV); process.exit(1); }

const head = rows.shift().map((h) => String(h).trim().toLowerCase());
const at = (r, name) => {
  const i = head.indexOf(name);
  return i >= 0 ? String(r[i] == null ? '' : r[i]).trim() : '';
};

const report = { added: [], filled: [], extra: [], drafts: [], skipped: 0, blank: 0 };

// A dry run reports and then throws away every write.
function DryRun() {}
try {
  db.tx(() => {
    const companies = [];
    const orphans = [];   // contact-only rows

    for (const r of rows) {
      const company = at(r, 'company name');
      const found = at(r, 'contact emails').match(ADDR);
      const email = found ? found[0] : '';
      if (!company && !email) { report.blank++; continue; }
      if (!company && email) { orphans.push({ email }); continue; }
      companies.push({ company, email, title: at(r, 'title'), first: at(r, 'first email'), fu1: at(r, 'follow-up 1') });
    }

    // ---- companies: create as a Lead, or fill gaps on the existing row
    for (const c of companies) {
      const id = P.kebab(c.company);
      let lead = db.one('SELECT * FROM leads WHERE id = ?', [id]);

      if (!lead) {
        const now = db.nowISO();
        db.run(
          `INSERT INTO leads (id, company, domain, contact_name, contact_title, contact_role,
             email, emails, source, stage, notes, tags, meta, currency, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,'leads',?,?,?,?,?,?)`,
          [id, c.company, c.email.includes('@') ? c.email.split('@')[1] : null,
           nameFromEmail(c.email) || null, c.title || null, roleOf(c.title),
           c.email || null, db.j([]), 'import', db.j([]), db.j([]), db.j({}), 'USD', now, now]
        );
        // The birth-stage trigger writes the first lead_stage_events row for us.
        report.added.push(id);
        lead = db.one('SELECT * FROM leads WHERE id = ?', [id]);
      } else {
        const sets = [];
        const vals = [];
        const fill = (col, val) => { if (val && !lead[col]) { sets.push(`${col} = ?`); vals.push(val); } };
        fill('email', c.email || null);
        fill('contact_title', c.title || null);
        fill('contact_name', nameFromEmail(c.email) || null);
        if (c.title && roleOf(c.title) !== 'other' && (!lead.contact_role || lead.contact_role === 'other')) {
          sets.push('contact_role = ?'); vals.push(roleOf(c.title));
        }
        if (sets.length) {
          sets.push('updated_at = ?'); vals.push(db.nowISO(), id);
          db.run(`UPDATE leads SET ${sets.join(', ')} WHERE id = ?`, vals);
          report.filled.push(id);
        }
      }

      // ---- the tracker's drafts, kept as drafts. No stage move, no fake send.
      for (const [body, label] of [[c.first, 'First Email'], [c.fu1, 'Follow-up 1']]) {
        if (!body) continue;
        const draftId = `${id}--${P.kebab(label)}`;
        const exists = db.val('SELECT id FROM drafts WHERE id = ?', [draftId]);
        if (exists) continue;
        const now = db.nowISO();
        db.run(
          `INSERT INTO drafts (id, lead_id, company, from_addr, to_addr, subject, body_text,
             status, campaign, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,'draft',?,?,?)`,
          [draftId, id, c.company, process.env.FROM_EMAIL || null, c.email || null,
           label, body, 'sep2-2026-backfill', now, now]
        );
        report.drafts.push(`${id} / ${label}`);
      }
    }

    // ---- contact-only rows: attach to the lead that owns the domain
    for (const o of orphans) {
      const host = o.email.split('@')[1] || '';
      const lead = db.all('SELECT id, domain, email, emails FROM leads').find(
        (l) => l.domain && (host === l.domain || host.endsWith('.' + l.domain))
      );
      if (!lead) { report.skipped++; continue; }
      const list = db.pj(lead.emails, []) || [];
      const known = [lead.email].concat(list).filter(Boolean).map((e) => String(e).toLowerCase());
      if (known.includes(o.email.toLowerCase())) continue;
      db.run('UPDATE leads SET emails = ?, updated_at = ? WHERE id = ?',
        [db.j(list.concat([o.email])), db.nowISO(), lead.id]);
      report.extra.push(`${lead.id} +${o.email}`);
    }

    if (!DRY) {
      db.logEvent({
        entity: 'system', entity_id: 'import-tracker', type: 'import',
        payload: { csv: CSV, added: report.added.length, filled: report.filled.length,
                   extra_emails: report.extra.length, drafts: report.drafts.length },
        at: db.nowISO(), actor: 'tools/import-tracker.cjs',
      });
    }

    if (DRY) throw new DryRun();
  });
} catch (e) {
  if (!(e instanceof DryRun)) throw e;
}

// ---------------------------------------------------------------- report
console.log('backfill  csv: ' + CSV);
console.log('backfill   db: ' + db.DB_PATH + (DRY ? '   [DRY RUN — nothing written]' : ''));
console.log('  new leads        : ' + report.added.length + (report.added.length ? '  ' + report.added.join(', ') : ''));
console.log('  fields filled    : ' + report.filled.length + (report.filled.length ? '  ' + report.filled.slice(0, 8).join(', ') : ''));
console.log('  extra emails     : ' + report.extra.length + (report.extra.length ? '  ' + report.extra.slice(0, 8).join(', ') : ''));
console.log('  drafts added     : ' + report.drafts.length + (report.drafts.length ? '  ' + report.drafts.slice(0, 8).join(', ') : ''));
console.log('  contact rows not matched to a lead: ' + report.skipped);
console.log('  blank rows skipped: ' + report.blank);
console.log('  total leads      : ' + db.val('SELECT COUNT(*) FROM leads') + '   drafts: ' + db.val('SELECT COUNT(*) FROM drafts'));
