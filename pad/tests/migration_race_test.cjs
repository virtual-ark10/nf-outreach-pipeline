#!/usr/bin/env node
/**
 * Boot race on the shared store (tests/migration_race_test.cjs).
 *
 * This pad and its leads engine open the SAME database — the store is selected by
 * OUTREACH_DB — and both run the same migration at boot. On a store that is missing a
 * column, both see it missing and both try to add it; the loser used to die with
 * "duplicate column name", and the same applied to the eight views, which were
 * created without IF NOT EXISTS. In production that reads as a service restarting
 * itself for no reason.
 *
 * Workers spin to a shared wall-clock instant so they really do collide, instead of
 * hoping a startup race shows up by itself.
 *
 * Run: node tests/migration_race_test.cjs        (offline, throwaway stores)
 */
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { Worker } = require('worker_threads');

const WORKERS = 4;
const ROUNDS = 4;
const DB_CJS = path.join(__dirname, '..', 'db.cjs');
let passed = 0;
let failed = 0;
const failures = [];
function check(name, fn) {
  try { fn(); passed += 1; console.log('  PASS  ' + name); }
  catch (err) { failed += 1; failures.push(name + ' :: ' + (err && err.message)); console.log('  FAIL  ' + name + '\n          ' + (err && err.message)); }
}

/** A store from before this build: events without attempts / last_error. */
function fixture(file) {
  const { DatabaseSync } = require('node:sqlite');
  const d = new DatabaseSync(file);
  d.exec(`CREATE TABLE events (
      id INTEGER PRIMARY KEY AUTOINCREMENT, entity TEXT, entity_id TEXT, type TEXT,
      payload TEXT, at TEXT, actor TEXT, processed_at TEXT);`);
  d.close();
}

const workerSrc = `
const { workerData, parentPort } = require('worker_threads');
while (Date.now() < workerData.start) { /* spin: collide on purpose */ }
let err = null;
try {
  process.env.OUTREACH_DB = workerData.db;      // read when db.cjs is required
  require(workerData.dbc).open();
} catch (e) { err = e.message; }
parentPort.postMessage(err);
`;

function round(n) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), `migrace${n}-`));
  const file = path.join(dir, 'outreach.db');
  fixture(file);
  const start = Date.now() + 300;
  const kids = Array.from({ length: WORKERS }, () => new Worker(workerSrc, {
    eval: true, workerData: { dbc: DB_CJS, db: file, start },
  }));
  return Promise.all(kids.map((w) => new Promise((res) => {
    w.on('message', (m) => res(m));
    w.on('error', (e) => res('worker error: ' + e.message));
  }))).then((errs) => {
    fs.rmSync(dir, { recursive: true, force: true });
    return errs.filter(Boolean);
  });
}

(async () => {
  const deaths = [];
  for (let i = 1; i <= ROUNDS; i += 1) deaths.push(...(await round(i)));
  check(`${WORKERS} processes x ${ROUNDS} rounds open one store without dying`, () => {
    if (deaths.length) throw new Error(`${deaths.length} of ${WORKERS * ROUNDS} died: ${deaths[0]}`);
  });

  // One process on its own must still migrate an old store.
  const solo = fs.mkdtempSync(path.join(os.tmpdir(), 'migrace-solo-'));
  const soloFile = path.join(solo, 'outreach.db');
  fixture(soloFile);
  process.env.OUTREACH_DB = soloFile;
  require('../db.cjs').open();
  const { DatabaseSync } = require('node:sqlite');
  const d = new DatabaseSync(soloFile, { readOnly: true });
  const cols = d.prepare('PRAGMA table_info(events)').all().map((c) => c.name);
  const views = d.prepare("SELECT name FROM sqlite_master WHERE type = 'view'").all().map((r) => r.name);
  check('a single process still migrates the old store', () => {
    for (const c of ['processed_at', 'attempts', 'last_error']) {
      if (!cols.includes(c)) throw new Error(`events.${c} missing`);
    }
    for (const v of ['v_events_pending', 'v_lead_pipeline', 'v_lead_timeline', 'v_followups_due', 'v_engagement_daily']) {
      if (!views.includes(v)) throw new Error(`view ${v} missing`);
    }
  });
  d.close();
  fs.rmSync(solo, { recursive: true, force: true });

  console.log('');
  console.log(`${passed} passed, ${failed} failed`);
  if (failures.length) { console.log('\nfailures:'); for (const f of failures) console.log('  - ' + f); }
  process.exit(failed ? 1 : 0);
})();
