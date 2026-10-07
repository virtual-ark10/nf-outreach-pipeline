#!/usr/bin/env node
'use strict';
// ============================================================================
//  tools/classify-auth-probes.cjs — label historic auth 401s as probe or token.
//
//  The pad's auth gate runs before routing, so an internet scanner asking for
//  /_next/server, /api/meta or /favicon.ico got the same 401 (and the same
//  events-table row) as a real browser carrying a stale token. That is why the
//  Tracking tab's failure list looked like a wall of "auth" errors: 298 rows,
//  essentially all scanner traffic.
//
//  New rows are labelled at write time by server.cjs (kind: 'probe' | 'token').
//  This tool applies the same rule to the rows written before that existed.
//
//  Usage:
//    node tools/classify-auth-probes.cjs --dry-run
//    node tools/classify-auth-probes.cjs
// ============================================================================

const path = require('path');
const db = require('../db.cjs');

// Same list as server.cjs PAD_API_SEGMENTS (keep in step with handleApi()).
const PAD_API_SEGMENTS = [
  'send', 'sent', 'received', 'drafts', 'config', 'session', 'hidden', 'tracking',
  'archive', 'events', 'redraft-guidance', 'health', 'webhook', 'domains', 'email', 'crm',
];

function kindFor(route) {
  const url = String(route || '').split(' ').slice(1).join(' ');   // "GET /api/sent?limit=100"
  const p = url.split('?')[0];
  if (p.indexOf('/api/') !== 0) return 'probe';
  const seg = p.split('/')[2] || '';
  return PAD_API_SEGMENTS.includes(seg) ? 'token' : 'probe';
}

const DRY = process.argv.includes('--dry-run');
const rows = db.all(
  "SELECT id, payload FROM events WHERE type = 'error' AND json_extract(payload, '$.op') = 'auth'"
);

const counts = { probe: 0, token: 0 };
const byRoute = new Map();
for (const row of rows) {
  const p = db.pj(row.payload, {}) || {};
  if (p.kind) { counts[p.kind] = (counts[p.kind] || 0) + 1; continue; }
  const kind = kindFor(p.route);
  counts[kind] += 1;
  const key = `${kind} :: ${p.route || '(no route)'}`;
  byRoute.set(key, (byRoute.get(key) || 0) + 1);
  if (DRY) continue;
  const next = Object.assign({}, p, { kind });
  db.run('UPDATE events SET payload = ? WHERE id = ?', [JSON.stringify(next), row.id]);
}

for (const [k, n] of [...byRoute.entries()].sort((a, b) => b[1] - a[1])) console.log(`  ${n}\t${k}`);
console.log(`summary ${JSON.stringify({ scanned: rows.length, probe: counts.probe, token: counts.token, dry_run: DRY })}`);
if (DRY) console.log('(dry run: nothing written)');
