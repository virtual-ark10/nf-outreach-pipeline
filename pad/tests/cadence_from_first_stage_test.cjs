#!/usr/bin/env node
/**
 * The cadence clock a first touch starts (tests/cadence_from_first_stage_test.cjs).
 *
 * A lead waits at stage 'leads' until its first touch is sent, and recordOutbound() writes
 * next_follow_up_at from the stage it was in at that moment. DUE_DAYS had no entry for
 * 'leads', so the column was written as null and the lead advanced to first_email with no
 * date. Nothing was ever "due": the ICP ladder's seed script requires a due date, the send
 * gate soft-flags a follow-up whose lead has none, and the whole second rung stalled.
 * Measured on 2026-10-22: all five first touches sent that morning had next_follow_up_at
 * null, and every ICP first touch is sent from 'leads'.
 *
 * The fallback is the wait of the stage the send advances INTO, so a first touch starts the
 * day-3 clock exactly as the ladder's own docs say.
 *
 * Run: node tests/cadence_from_first_stage_test.cjs        (offline, throwaway store)
 */
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'nf-cadence-'));
process.env.OUTREACH_DB = path.join(tmp, 'outreach.db');

let passed = 0;
const failures = [];
function check(name, got, want) {
  if (JSON.stringify(got) === JSON.stringify(want)) { passed++; return; }
  failures.push(`${name}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
}

const pipeline = require(path.join(__dirname, '..', 'pipeline.cjs'));
const AT = '2026-10-22T07:00:00.000Z';
const days = (n) => new Date(new Date(AT).getTime() + n * 86400000).toISOString();

// The bug: a send from the first stage starts the first_email wait.
check('leads -> +3 days', pipeline.nextFollowUp('leads', AT), days(3));
// The rungs it already handled are unchanged.
check('first_email -> +3 days', pipeline.nextFollowUp('first_email', AT), days(3));
check('follow_up_1 -> +4 days', pipeline.nextFollowUp('follow_up_1', AT), days(4));
check('follow_up_2 -> +7 days', pipeline.nextFollowUp('follow_up_2', AT), days(7));
// No clock after the last rung, and never a date from an unparseable stage.
check('follow_up_3 -> none', pipeline.nextFollowUp('follow_up_3', AT), null);
check('won -> none', pipeline.nextFollowUp('won', AT), null);

fs.rmSync(tmp, { recursive: true, force: true });

if (failures.length) {
  console.error('FAIL');
  for (const f of failures) console.error('  ' + f);
  process.exit(1);
}
console.log(`ok ${passed} cadence checks`);
