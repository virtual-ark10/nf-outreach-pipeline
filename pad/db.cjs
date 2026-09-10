'use strict';
// ============================================================================
//  db.cjs — the single SQLite store for the outreach pipeline.
//
//  One file holds everything the pad and the leads engine used to keep in JSON:
//    leads, lead_stage_events, emails, replies, drafts, events   (+ 3 views)
//  See schema.sql for the field-by-field reasoning.
//
//  Both processes open the same file: the pad (port 3001) and the leads engine
//  (port 3002). WAL + busy_timeout make that safe — one writer at a time, readers
//  never blocked. Override the location with OUTREACH_DB.
//
//  Usage:
//    const db = require('./db.cjs');
//    db.all('SELECT * FROM leads WHERE stage = ?', ['leads']);
// ============================================================================

const fs = require('fs');
const path = require('path');
const { DatabaseSync } = require('node:sqlite');

const DB_PATH = process.env.OUTREACH_DB || path.join(__dirname, 'data', 'outreach.db');
const SCHEMA_PATH = path.join(__dirname, 'schema.sql');

let _db = null;
let _depth = 0;      // transaction nesting depth (tx() is reentrant via SAVEPOINT)

function open() {
  if (_db) return _db;
  fs.mkdirSync(path.dirname(DB_PATH), { recursive: true });
  const db = new DatabaseSync(DB_PATH);
  db.exec('PRAGMA journal_mode = WAL');
  db.exec('PRAGMA foreign_keys = ON');
  db.exec('PRAGMA busy_timeout = 5000');
  db.exec('PRAGMA synchronous = NORMAL');
  db.exec(fs.readFileSync(SCHEMA_PATH, 'utf8'));   // idempotent: every object is IF NOT EXISTS
  _db = db;
  return _db;
}

// STRICT tables reject booleans and undefined, so every bound value goes through
// here: undefined -> NULL, boolean -> 0/1. Anything else is passed as-is, which
// keeps a wrong type loud instead of silently coercing it.
function bind(args) {
  return (args || []).map((v) => {
    if (v === undefined) return null;
    if (typeof v === 'boolean') return v ? 1 : 0;
    if (v instanceof Date) return v.toISOString();
    return v;
  });
}
const plain = (row) => (row ? Object.assign({}, row) : row);

// Callers may pass params variadically — one(sql, 'a', 1) — or as a single array —
// one(sql, ['a', 1]). Both land here as a flat list.
function flat(params) {
  return (params.length === 1 && Array.isArray(params[0])) ? params[0] : params;
}

function all(sql, ...params) { return open().prepare(sql).all(...bind(flat(params))).map(plain); }
function one(sql, ...params) { const r = open().prepare(sql).get(...bind(flat(params))); return r ? plain(r) : null; }
function run(sql, ...params) { return open().prepare(sql).run(...bind(flat(params))); }
function exec(sql) { return open().exec(sql); }
function val(sql, ...params) { const r = one(sql, ...params); return r ? Object.values(r)[0] : null; }
function tx(fn) {
  const db = open();
  const nested = _depth > 0;
  const name = 'sp_' + _depth;
  db.exec(nested ? `SAVEPOINT ${name}` : 'BEGIN IMMEDIATE');
  _depth++;
  try {
    const out = fn(db);
    db.exec(nested ? `RELEASE ${name}` : 'COMMIT');
    return out;
  } catch (e) {
    try { db.exec(nested ? `ROLLBACK TO ${name}; RELEASE ${name}` : 'ROLLBACK'); } catch (_) {}
    throw e;
  } finally { _depth--; }
}

// ---------------------------------------------------------------- small utils
const nowISO = () => new Date().toISOString();
function j(v) {                       // store as JSON text (NULL stays NULL)
  if (v === undefined || v === null) return null;
  if (typeof v === 'string') return JSON.stringify(v);
  try { return JSON.stringify(v); } catch (e) { return null; }
}
function pj(v, fallback) {            // read JSON text back
  if (v === null || v === undefined) return fallback;
  try { return JSON.parse(v); } catch (e) { return fallback; }
}
const bit = (v) => (!v || v === '0' || v === 'false' || v === 0 ? 0 : 1);
function int(v) { const n = parseInt(v, 10); return Number.isFinite(n) ? n : null; }
const csvList = (v) => (Array.isArray(v) ? v.filter(Boolean).join(', ') : (v == null ? null : String(v)));

// ---------------------------------------------------------------- address match
// A lead owns every address we know for it: the primary plus the emails array.
const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;
function addressesOf(lead) {
  const out = new Set();
  [lead.email, ...(pj(lead.emails, []) || [])].forEach((e) => {
    if (e) String(e).toLowerCase().split(/[,\s;]+/).forEach((a) => { const m = a.match(EMAIL_RE); if (m) out.add(m[0].toLowerCase()); });
  });
  return out;
}
function findLeadByAddress(addr) {
  const m = String(addr || '').match(EMAIL_RE);
  const a = (m ? m[0] : '').toLowerCase();
  if (!a) return null;
  const direct = one('SELECT * FROM leads WHERE lower(email) = ? AND deleted_at IS NULL LIMIT 1', [a]);
  if (direct) return direct;
  // secondary addresses live in the JSON array
  return one(
    `SELECT * FROM leads WHERE deleted_at IS NULL AND emails IS NOT NULL
       AND EXISTS (SELECT 1 FROM json_each(leads.emails) WHERE lower(value) = ?) LIMIT 1`, [a]);
}

// ---------------------------------------------------------------- audit trails
function logEvent({ entity, entity_id, type, payload, actor, at }) {
  return run(
    'INSERT INTO events (entity, entity_id, type, payload, at, actor) VALUES (?, ?, ?, ?, ?, ?)',
    [entity, entity_id == null ? null : String(entity_id), type, j(payload), at || nowISO(), actor || null]
  );
}

// ---------------------------------------------------------------- failures
// A failure is a first-class event, not just a log line. Anything that throws or
// comes back non-2xx on the way to doing real work writes a type='error' row so
// the failure lands in the same stream as the successes (countable, chartable,
// and visible next to the lead it concerns). payload.op says WHICH operation
// failed, so one type stays easy to aggregate: SELECT op, COUNT(*) ...
// This must never throw — recording a failure must not create one.
function logFailure({ entity = 'system', entity_id = null, op, error = null, status = null, actor = null, at = null, extra = null }) {
  const message = String((error && error.message) || error || 'unknown error');
  let payload;
  try {
    payload = j(Object.assign({ op: op || 'unknown', message: message.slice(0, 900), status: status == null ? null : status }, extra || {}));
  } catch (e) {
    payload = j({ op: op || 'unknown', message: message.slice(0, 900) });
  }
  try {
    return run(
      'INSERT INTO events (entity, entity_id, type, payload, at, actor) VALUES (?, ?, ?, ?, ?, ?)',
      [entity, entity_id == null ? null : String(entity_id), 'error', payload, at || nowISO(), actor || null]
    );
  } catch (e) {
    try { console.error('[db] could not record a failure event:', e && e.message); } catch (_) { /* nothing left to do */ }
    return null;
  }
}
function stageEvent({ lead_id, from_stage, to_stage, at, by, note, source }) {
  return run(
    'INSERT INTO lead_stage_events (lead_id, from_stage, to_stage, at, by, note, source) VALUES (?, ?, ?, ?, ?, ?, ?)',
    [lead_id, from_stage, to_stage, at || nowISO(), by || null, note || null, source || null]
  );
}
// The one way a stage ever moves: history row + leads row in one transaction.
function setStage(lead_id, to_stage, { by = 'crm', note = null, source = null } = {}) {
  return tx(() => {
    const lead = one('SELECT id, stage FROM leads WHERE id = ?', [lead_id]);
    if (!lead) throw new Error('unknown lead: ' + lead_id);
    const at = nowISO();
    run('UPDATE leads SET stage = ?, updated_at = ?, stage_changed_at = ? WHERE id = ?', [to_stage, at, at, lead_id]);
    stageEvent({ lead_id, from_stage: lead.stage, to_stage, at, by, note, source });
    return at;
  });
}

// ---------------------------------------------------------------- NF-derived
const DUE_DAYS = Object.assign(
  { first_email: 3, follow_up_1: 4, follow_up_2: 7, follow_up_3: 0 },
  (() => { try { return JSON.parse(process.env.LEAD_DUE_DAYS || '{}'); } catch (e) { return {}; } })(),
);
const DAY = 86400000;
// Everything the UI shows next to a lead, computed from the tables (the old
// decorate() over an in-memory activity array, now three indexed COUNTs).
function derive(lead) {
  const out = one("SELECT COUNT(*) n, MAX(sent_at) last FROM emails WHERE lead_id = ? AND direction = 'outbound'", [lead.id]) || {};
  const inb = one("SELECT COUNT(*) n FROM replies WHERE lead_id = ? AND deleted_at IS NULL", [lead.id]) || {};
  const clicks = val("SELECT COUNT(*) FROM events WHERE entity = 'lead' AND entity_id = ? AND type = 'click'", [lead.id]) || 0;
  const lastEv = val('SELECT MAX(at) FROM v_lead_timeline WHERE lead_id = ?', [lead.id]);
  const wait = DUE_DAYS[lead.stage];
  // The persisted next_follow_up_at (written on send) is authoritative; the cadence
  // is only the fallback for rows that predate it.
  const computed = (lead.stage !== 'leads' && wait && out.last)
    ? new Date(new Date(out.last).getTime() + wait * DAY).toISOString() : null;
  const due = lead.next_follow_up_at || computed;
  return {
    emails_sent: out.n || 0,
    emails_received: inb.n || 0,
    clicks: clicks || 0,
    last_activity: lastEv || null,
    last_email_out: out.last || null,
    next_due: due,
    overdue: Boolean(due && new Date(due) < new Date()),
    days_since_out: out.last ? Math.floor((Date.now() - new Date(out.last)) / DAY) : null,
  };
}

module.exports = {
  DB_PATH, SCHEMA_PATH, open, all, one, run, exec, val, tx, plain, bind,
  nowISO, j, pj, bit, int, csvList, addressesOf, findLeadByAddress,
  logEvent, logFailure, stageEvent, setStage, derive, EMAIL_RE, DUE_DAYS,
};

if (require.main === module) {
  open();
  console.log('db:', DB_PATH);
  console.log(open().prepare("SELECT name, type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name").all()
    .map((r) => `${r.type.padEnd(6)} ${r.name}`).join('\n'));
}
