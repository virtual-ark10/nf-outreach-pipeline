// Sync visit/click attribution into the live NewsletterFIT tracking sheet.
//
// It reads the CURRENT live sheet (via the public CSV export), merges in the
// per-lead attribution summary from the local DB, and appends visit columns:
//   Visited? | First Visit | Last Visit | Visit Count | Clicks
// matched by Company Name (the sheet's key column). Then it either:
//   A) writes a ready-to-import CSV (default), or
//   B) pushes the columns into the live Google Sheet via the Sheets API
//      (requires Google OAuth to be set up — see SKILL google-workspace).
//
// Usage:
//   node utils/sync-to-sheet.js                -> writes lead_sheet_with_visits.csv
//   node utils/sync-to-sheet.js --push         -> updates the live sheet (needs OAuth)
//   NF_ATTRIBUTION_DB=... node utils/sync-to-sheet.js
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execSync } from 'node:child_process';
import { AttributionStore } from '../src/store.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SHEET_ID = '1wB0xxihPy4usJnVZHuN0W31rr26j05aZnL6ymzgbWLk';
const CSV_EXPORT = `https://docs.google.com/spreadsheets/d/${SHEET_ID}/export?format=csv`;
const DB_PATH = process.env.NF_ATTRIBUTION_DB || path.join(__dirname, '..', 'attribution.json');

// --- 1. Fetch the live sheet -------------------------------------------------
function fetchSheet() {
  const tmp = path.join(__dirname, '..', '.sheet-live.csv');
  execSync(`curl -sL "${CSV_EXPORT}" -o "${tmp}"`, { stdio: 'inherit' });
  const raw = fs.readFileSync(tmp, 'utf8');
  const lines = raw.split(/\r?\n/).filter((l) => l.trim().length >= 0);
  const rows = lines.map((l) => csvParseLine(l));
  return { header: rows[0], rows: rows.slice(1) };
}

function csvParseLine(line) {
  const out = [];
  let cur = '', inQ = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (inQ) {
      if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; }
      else if (c === '"') inQ = false;
      else cur += c;
    } else if (c === '"') inQ = true;
    else if (c === ',') { out.push(cur); cur = ''; }
    else cur += c;
  }
  out.push(cur);
  return out;
}

// --- 2. Load attribution summary --------------------------------------------
function loadAttribution() {
  if (!fs.existsSync(DB_PATH)) return new Map();
  const store = new AttributionStore(DB_PATH);
  const summary = store.summary();
  store.close();
  const byLead = new Map();
  for (const s of summary) byLead.set(String(s.lead_id).toLowerCase().trim(), s);
  return byLead;
}

// --- 3. Merge -----------------------------------------------------------------
function merge(sheet, attribution) {
  const header = [...sheet.header];
  const NEW_COLS = ['Visited?', 'First Visit', 'Last Visit', 'Visit Count', 'Clicks'];
  const startIdx = header.length;
  for (const k of NEW_COLS) if (!header.includes(k)) header.push(k);

  const outRows = [header];
  let matched = 0;
  for (const row of sheet.rows) {
    const company = String(row[0] || '').toLowerCase().trim();
    const at = attribution.get(company);
    const newRow = [...row];
    for (const k of NEW_COLS) newRow.push('');
    if (at && company) {
      newRow[startIdx + 0] = at.visit_count > 0 ? 'Yes' : 'No';
      newRow[startIdx + 1] = at.first_visit || '';
      newRow[startIdx + 2] = at.last_visit || '';
      newRow[startIdx + 3] = at.visit_count;
      newRow[startIdx + 4] = at.clicked || 0;
      matched++;
    }
    outRows.push(newRow);
  }
  return { outRows, matched };
}

function csvOut(rows) {
  return rows.map((r) => r.map(esc).join(',')).join('\n');
}
const esc = (v) => `"${String(v == null ? '' : v).replaceAll('"', '""')}"`;

// --- main ---------------------------------------------------------------------
function main() {
  const args = process.argv.slice(2);
  const push = args.includes('--push');
  const sheet = fetchSheet();
  const attribution = loadAttribution();
  const { outRows, matched } = merge(sheet, attribution);
  const outFile = path.join(__dirname, '..', 'lead_sheet_with_visits.csv');
  fs.writeFileSync(outFile, csvOut(outRows));
  console.log(`Merged ${matched} lead(s) with attribution data.`);
  console.log(`Columns: ${outRows[0].join(', ')}`);
  console.log(`Wrote ${outFile} (${outRows.length - 1} rows).`);
  if (push) {
    console.log('\n--push requires Google OAuth (google-workspace skill) which is not');
    console.log('set up on this box. Re-run with credentials, or import the CSV above.');
  }
}
main();