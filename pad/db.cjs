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
  migrate(db);                                     // then the columns CREATE TABLE cannot add
  _db = db;
  return _db;
}

// A store that already exists keeps its shape: CREATE TABLE IF NOT EXISTS is a no-op
// on it, so anything added to a table LATER has to be applied as an ALTER here, on
// every open, idempotently. Right now that is the event-queue trio plus the views
// that depend on it.
function migrate(db) {
  const cols = (t) => db.prepare(`PRAGMA table_info(${t})`).all().map((r) => r.name);
  const add = (t, name, decl) => {
    if (!cols(t).includes(name)) db.exec(`ALTER TABLE ${t} ADD COLUMN ${name} ${decl}`);
  };
  add('events', 'processed_at', 'TEXT');
  add('events', 'attempts', 'INTEGER NOT NULL DEFAULT 0');
  add('events', 'last_error', 'TEXT');
  // The index only exists once the column does, which is why it is not in schema.sql.
  db.exec('CREATE INDEX IF NOT EXISTS ix_events_pending ON events(processed_at, id)');
  db.exec('DROP VIEW IF EXISTS v_events_pending');
  db.exec(`CREATE VIEW v_events_pending AS
    SELECT id, entity, entity_id, type, payload, at, actor, attempts, last_error
      FROM events WHERE processed_at IS NULL ORDER BY id`);
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

// ---------------------------------------------------------------- engagement
// Opens and clicks, as Resend reports them (see the email_engagements table in
// schema.sql). This is a record of something that already happened at the
// provider, so it never advances a stage and never touches emails.status — it
// would be a second writer of both, which is exactly the invariant this engine
// is built on.
//
// `dedupe` collapses a webhook retry. Resend sends no event id, so the caller
// derives one from the payload's stable parts; identical retries become one row
// through the unique index, and the counters stay honest.
function recordEngagement({ resend_id, kind, url = null, user_agent = null, ip = null, at = null, dedupe = null }) {
  if (!resend_id || !kind) return { inserted: 0, reason: 'resend_id and kind are required' };
  const when = at || nowISO();
  return tx(() => {
    const mail = one('SELECT id, lead_id FROM emails WHERE resend_id = ?', [resend_id]);
    let host = null;
    try { host = url ? new URL(url).host.toLowerCase() : null; } catch (e) { host = null; }
    const key = dedupe || [kind, resend_id, url || '', when].join('|');
    const ins = run(
      `INSERT OR IGNORE INTO email_engagements
         (resend_id, email_id, lead_id, kind, url, link_host, user_agent, ip, at, dedupe, created_at)
       VALUES (?,?,?,?,?,?,?,?,?,?,?)`,
      [resend_id, mail ? mail.id : null, mail ? mail.lead_id : null, kind, url, host,
       user_agent ? String(user_agent).slice(0, 400) : null, ip || null, when, key, nowISO()]
    );
    return {
      inserted: ins.changes || 0,
      duplicate: !ins.changes,
      email_id: mail ? mail.id : null,
      lead_id: mail ? mail.lead_id : null,
      matched: Boolean(mail),
      kind,
      url,
    };
  });
}

// The three reads the Tracking tab needs. Counters are always derived here, so
// they can be rebuilt from the rows and can never drift from them.
function engagementTotals(sinceISO) {
  return one(
    `SELECT COUNT(*)                                                        AS events,
            COALESCE(SUM(CASE WHEN kind = 'open'  THEN 1 ELSE 0 END), 0)     AS opens,
            COALESCE(SUM(CASE WHEN kind = 'click' THEN 1 ELSE 0 END), 0)     AS email_clicks,
            COUNT(DISTINCT CASE WHEN kind = 'open'  THEN resend_id END)      AS opened_messages,
            COUNT(DISTINCT CASE WHEN kind = 'click' THEN resend_id END)      AS clicked_messages,
            COUNT(DISTINCT CASE WHEN kind = 'open'  THEN lead_id END)        AS opened_leads,
            COUNT(DISTINCT CASE WHEN kind = 'click' THEN lead_id END)        AS clicked_leads
       FROM email_engagements WHERE at >= ?`, [sinceISO]) || {};
}
function engagementSeries(sinceISO) {
  return all(
    `SELECT substr(at, 1, 10) AS day,
            SUM(CASE WHEN kind = 'open'  THEN 1 ELSE 0 END) AS opens,
            SUM(CASE WHEN kind = 'click' THEN 1 ELSE 0 END) AS clicks
       FROM email_engagements WHERE at >= ? GROUP BY day ORDER BY day`, [sinceISO]);
}
function engagementByLead(sinceISO) {
  return all(
    `SELECT lead_id,
            SUM(CASE WHEN kind = 'open'  THEN 1 ELSE 0 END) AS opens,
            SUM(CASE WHEN kind = 'click' THEN 1 ELSE 0 END) AS email_clicks
       FROM email_engagements WHERE at >= ? AND lead_id IS NOT NULL
      GROUP BY lead_id`, [sinceISO]);
}
function topLinks(sinceISO, limit = 10) {
  return all(
    `SELECT url, link_host AS host, COUNT(*) AS clicks, COUNT(DISTINCT lead_id) AS leads, MAX(at) AS last_at
       FROM email_engagements
      WHERE kind = 'click' AND at >= ? AND url IS NOT NULL
      GROUP BY url ORDER BY clicks DESC, last_at DESC LIMIT ?`, [sinceISO, limit]);
}
function engagementForLead(leadId) {
  return one('SELECT * FROM v_engagement_by_lead WHERE lead_id = ?', [leadId]) || { opens: 0, email_clicks: 0 };
}

// ---------------------------------------------------------------- event queue
// events is the audit trail AND the work queue. A row written by one process is
// drained by whichever process handles the next request, so the pad and the engine
// react to each other without a message bus.
//
// The rules that drain it live in hooks.cjs and may only add notes, flags, reasons
// and scheduling. Stage transitions belong to pipeline.cjs — the single writer of
// `stage` — and the `store` facade handed to a rule deliberately has NO setStage, so
// a rule cannot move a stage even by accident. That is what lets a rule table exist
// without becoming a second brain that double-advances every send.
//
// Claim, apply and mark happen in ONE transaction: a rule either runs and is marked,
// or throws and is retried later. A rule that keeps throwing cannot stall the queue
// either — attempts and last_error are recorded, and after maxAttempts the row is
// dead-lettered (marked processed with the error kept, plus one type='error' event)
// so everything behind it still moves.
function processEvents(rules, { limit = 200, maxAttempts = 5 } = {}) {
  if (!rules) return { processed: 0, actions: [], skipped: 'no rule table' };
  const rows = all(
    `SELECT id, entity, entity_id, type, payload, at, attempts FROM events
      WHERE processed_at IS NULL ORDER BY id LIMIT ?`, [limit]);
  if (!rows.length) return { processed: 0, actions: [] };

  const store = { one, all, run, val, logEvent, logFailure, nowISO, j, pj };  // no setStage, on purpose
  const actions = [];
  try {
    open().exec('BEGIN IMMEDIATE');
  } catch (e) {
    // Someone else is writing the same file; the next request drains instead.
    return { processed: 0, actions: [], deferred: true };
  }
  try {
    for (const row of rows) {
      const payload = pj(row.payload, {}) || {};
      const rule = rules[row.type];
      let outcome = null;
      try {
        if (rule) outcome = rule({ event: row, payload, store });
      } catch (err) {
        const attempts = (row.attempts || 0) + 1;
        const msg = String((err && err.message) || err).slice(0, 500);
        if (attempts >= maxAttempts) {
          run('UPDATE events SET processed_at = ?, attempts = ?, last_error = ? WHERE id = ?',
            [nowISO(), attempts, msg, row.id]);
          logFailure({
            entity: 'event', entity_id: String(row.id), op: 'rule_' + row.type, error: err,
            actor: 'hooks', extra: { event_id: row.id, attempts, dead_lettered: true },
          });
          actions.push({ id: row.id, event: row.type, dead_lettered: true, attempts, error: msg });
        } else {
          run('UPDATE events SET attempts = ?, last_error = ? WHERE id = ?', [attempts, msg, row.id]);
          actions.push({ id: row.id, event: row.type, retry: attempts, error: msg });
        }
        continue;
      }
      run('UPDATE events SET processed_at = ? WHERE id = ?', [nowISO(), row.id]);
      if (outcome) actions.push(Object.assign({ id: row.id, event: row.type }, outcome));
    }
    open().exec('COMMIT');
  } catch (e) {
    try { open().exec('ROLLBACK'); } catch (_) { /* nothing left to do */ }
    throw e;
  }
  return { processed: rows.length, actions };
}
function pendingEvents(limit = 200) {
  return all('SELECT id, entity, entity_id, type, payload, at, actor, attempts, last_error FROM v_events_pending LIMIT ?', [limit]);
}
function recentEvents({ limit = 100, leadId } = {}) {
  const where = [];
  const vals = [];
  if (leadId) { where.push('entity_id = ?'); vals.push(String(leadId)); }
  return all(`SELECT id, entity, entity_id, type, payload, at, actor, processed_at, attempts, last_error
                FROM events ${where.length ? 'WHERE ' + where.join(' AND ') : ''} ORDER BY id DESC LIMIT ?`,
    ...vals, limit);
}

// ---------------------------------------------------------------- redraft
// Why a draft came back. Rows so the guidance can be counted, and the
// draft.redraft_requested rule picks these up from the queue.
function recordRedraft({ draft_id = null, lead_id = null, reason = null, note = null, at = null }) {
  const when = at || nowISO();
  return tx(() => {
    const ins = run(
      'INSERT INTO redraft_notes (draft_id, lead_id, reason, note, created_at) VALUES (?,?,?,?,?)',
      [draft_id, lead_id, reason, note, when]);
    logEvent({
      entity: 'draft', entity_id: draft_id, type: 'draft.redraft_requested',
      payload: { reason: reason || null, note: note || null, lead_id }, at: when, actor: 'pad',
    });
    return { id: Number(ins.lastInsertRowid), reason, note };
  });
}
function redraftGuidance({ limit = 8 } = {}) {
  const reasons = all('SELECT reason, n, last_at FROM v_redraft_reasons LIMIT ?', [limit]);
  const recent = all('SELECT draft_id, lead_id, reason, note, created_at FROM redraft_notes ORDER BY id DESC LIMIT 20');
  const top = reasons.map((r) => `${r.reason} (${r.n})`).join(', ');
  return { reasons, recent, summary: top ? `Redraft reasons so far: ${top}.` : 'No redraft feedback yet.' };
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
  const eng = engagementForLead(lead.id);
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
    clicks: clicks || 0,                      // first-party: minted site links that were clicked
    opens: eng.opens || 0,                    // Resend open tracking (pixel on the brand's tracking subdomain)
    email_clicks: eng.email_clicks || 0,      // Resend click tracking — the link in the mail itself
    first_open_at: eng.first_open_at || null,
    last_open_at: eng.last_open_at || null,
    first_click_at: eng.first_click_at || null,
    last_click_at: eng.last_click_at || null,
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
  recordEngagement, engagementTotals, engagementSeries, engagementByLead, topLinks, engagementForLead,
  processEvents, pendingEvents, recentEvents, recordRedraft, redraftGuidance, migrate,
};

if (require.main === module) {
  open();
  console.log('db:', DB_PATH);
  console.log(open().prepare("SELECT name, type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name").all()
    .map((r) => `${r.type.padEnd(6)} ${r.name}`).join('\n'));
}
