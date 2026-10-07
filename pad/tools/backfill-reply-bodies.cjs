#!/usr/bin/env node
'use strict';
// ============================================================================
//  tools/backfill-reply-bodies.cjs — fill in reply bodies that were never stored.
//
//  Why this exists: Resend's `email.received` webhook is metadata only (from, to,
//  subject, ids). Until the webhook learned to fetch the body
//  (hydrateInbound() in server.cjs), every inbound reply was recorded with a
//  subject and an empty body, so the pad showed a blank message and the intel
//  loop had nothing to read. This walks those rows and repairs them from
//  GET /emails/receiving/:id.
//
//  Safe by construction:
//    - it only ever writes where the column is still empty (never overwrites a
//      body a human or the webhook already stored);
//    - it never deletes or rewrites a row's identity, timestamps or stage;
//    - --dry-run reports what it would do and changes nothing.
//
//  Usage:
//    node tools/backfill-reply-bodies.cjs [--dry-run] [--limit N] [--id REPLY_ID]
//
//  Reads RESEND_API_KEY from the environment or from the pad's .env.
// ============================================================================

const fs = require('fs');
const path = require('path');

function loadEnv(file) {
  if (!fs.existsSync(file)) return;
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$/);
    if (!m) continue;
    let v = m[2];
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) v = v.slice(1, -1);
    if (process.env[m[1]] === undefined) process.env[m[1]] = v;
  }
}
loadEnv(path.join(__dirname, '..', '.env'));

const db = require('../db.cjs');

const args = process.argv.slice(2);
const has = (f) => args.includes(f);
const val = (f) => { const i = args.indexOf(f); return i >= 0 ? args[i + 1] : null; };
const DRY = has('--dry-run');
const LIMIT = Number(val('--limit') || 0) || null;
const ONE = val('--id');
const KEY = process.env.RESEND_API_KEY || '';

if (!KEY) { console.error('RESEND_API_KEY not set (env or pad .env)'); process.exit(2); }

// The event payload is stored verbatim in replies.raw; the Resend email id is the
// only key that reaches the receiving endpoint. Two stored shapes exist: the
// webhook event itself (data.email_id), and the older JSONL archive rows imported
// by migrate-json.cjs, which nested the event under `event` (event.data.email_id).
function emailIdFromRaw(raw) {
  if (!raw) return null;
  let j = raw;
  if (typeof raw === 'string') { try { j = JSON.parse(raw); } catch (e) { return null; } }
  if (!j || typeof j !== 'object') return null;
  const d = (j.data && typeof j.data === 'object') ? j.data : {};
  const ev = (j.event && typeof j.event === 'object') ? j.event : {};
  const ed = (ev.data && typeof ev.data === 'object') ? ev.data : {};
  return d.email_id || ed.email_id || j.email_id || null;
}

async function fetchBody(id) {
  const r = await fetch(`https://api.resend.com/emails/receiving/${encodeURIComponent(id)}`, {
    headers: { Authorization: `Bearer ${KEY}` },
  });
  if (!r.ok) throw new Error(`Resend returned ${r.status}`);
  const j = await r.json();
  if (!j.text && !j.html) throw new Error('no text/html part');
  return { text: j.text || null, html: j.html || null };
}

// Same rule as server.cjs hydrateReplyBody: only an EMPTY column is written.
function hydrate(replyId, body) {
  const sets = [], vals = [];
  if (body.text) { sets.push("body_text = COALESCE(NULLIF(body_text,''), ?)"); vals.push(body.text); }
  if (body.html) { sets.push("body_html = COALESCE(NULLIF(body_html,''), ?)"); vals.push(body.html); }
  if (!sets.length) return { updated: 0, email_updated: 0 };
  const r = db.run(`UPDATE replies SET ${sets.join(', ')} WHERE id = ?`, vals.concat(replyId));
  const em = db.run(`UPDATE emails SET ${sets.join(', ')} WHERE id = (SELECT email_id FROM replies WHERE id = ?)`, vals.concat(replyId));
  return { updated: r.changes, email_updated: em.changes };
}

async function main() {
  const where = ONE
    ? 'id = ?'
    : "(body_text IS NULL OR body_text = '') AND (body_html IS NULL OR body_html = '')";
  const params = ONE ? [Number(ONE)] : [];
  const rows = db.all(
    `SELECT id, lead_id, from_addr, subject, received_at, raw FROM replies WHERE ${where} ORDER BY received_at DESC` +
    (LIMIT ? ` LIMIT ${Number(LIMIT)}` : ''),
    params
  );

  const report = { checked: rows.length, healed: 0, already: 0, no_raw: 0, failed: 0, dry_run: DRY, details: [] };
  for (const row of rows) {
    const emailId = emailIdFromRaw(row.raw);
    if (!emailId) {
      report.no_raw++;
      report.details.push({ reply_id: row.id, subject: row.subject, result: 'no email_id in raw payload' });
      continue;
    }
    try {
      const body = await fetchBody(emailId);
      const bytes = (body.text || '').length;
      if (DRY) {
        report.healed++;
        report.details.push({ reply_id: row.id, lead_id: row.lead_id, subject: row.subject, result: 'would heal', text_chars: bytes });
        continue;
      }
      const out = hydrate(row.id, body);
      if (out.updated) {
        report.healed++;
        report.details.push({ reply_id: row.id, lead_id: row.lead_id, subject: row.subject, result: 'healed', text_chars: bytes, email_row: out.email_updated });
      } else {
        report.already++;
        report.details.push({ reply_id: row.id, subject: row.subject, result: 'already had a body' });
      }
    } catch (e) {
      report.failed++;
      report.details.push({ reply_id: row.id, subject: row.subject, result: 'FAILED: ' + e.message, email_id: emailId });
    }
  }

  for (const d of report.details) console.log(JSON.stringify(d));
  const summary = { checked: report.checked, healed: report.healed, already: report.already, no_raw: report.no_raw, failed: report.failed, dry_run: DRY };
  console.log('summary ' + JSON.stringify(summary));
  process.exit(report.failed ? 1 : 0);
}

main().catch((e) => { console.error('backfill failed:', e && e.message); process.exit(1); });
