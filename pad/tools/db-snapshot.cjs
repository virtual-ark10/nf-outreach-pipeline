#!/usr/bin/env node
'use strict';
// ============================================================================
//  tools/db-snapshot.cjs — a consistent copy of the live database.
//
//  VACUUM INTO writes a complete, defragmented, transactionally-consistent copy
//  of the source database, including whatever is still in the WAL. Copying the
//  .db file with cp/rsync while the services are running can miss committed
//  transactions sitting in -wal; this cannot.
//
//  Usage: node tools/db-snapshot.cjs /path/to/target.db
// ============================================================================

const fs = require('fs');
const path = require('path');
const db = require('../db.cjs');

const target = process.argv[2];
if (!target) {
  console.error('usage: node tools/db-snapshot.cjs <target.db>');
  process.exit(2);
}
const out = path.resolve(target);
// Guard: everything below is destructive (unlink + VACUUM INTO). That is only safe
// when the target is NOT the live database. In this deployment resend-pad/data IS
// pad/data (the live dir is a checkout of this repo), so snapshot-state.sh used to
// hand us the live path itself: the unlink below then deleted the running database
// out from under the pad and the leads engine (2026-09-26 incident). Refuse loudly
// instead — pass a target outside the live data dir.
const live = path.resolve(db.DB_PATH);
const sameFile = (a, b) => {
  if (a === b) return true;
  try {
    const sa = fs.statSync(a), sb = fs.statSync(b);
    return sa.ino === sb.ino && sa.dev === sb.dev;
  } catch (e) { return false; }
};
if (sameFile(out, live)) {
  console.error(`refusing: target ${out} IS the live database (${live}).`);
  console.error('pass a path outside the live data dir (e.g. a separate snapshot checkout)');
  process.exit(3);
}
fs.mkdirSync(path.dirname(out), { recursive: true });
for (const suffix of ['', '-wal', '-shm']) {
  try { fs.unlinkSync(out + suffix); } catch (e) { /* nothing to remove */ }
}
db.open();
db.exec(`VACUUM INTO '${out.replace(/'/g, "''")}'`);

const size = fs.statSync(out).size;
const counts = {};
const src = db.open();
for (const t of ['leads', 'lead_stage_events', 'emails', 'replies', 'drafts', 'events']) {
  counts[t] = src.prepare(`SELECT COUNT(*) n FROM ${t}`).get().n;
}
console.log(`snapshot -> ${out} (${(size / 1024).toFixed(0)} KB)`);
console.log('rows:', JSON.stringify(counts));
