// Exports visit data so it can feed the lead tracking sheet.
// Usage:
//   node utils/export-visits.js                 -> prints per-lead summary table
//   node utils/export-visits.js --csv out       -> writes out/visits.csv + out/summary.csv
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { AttributionStore } from '../src/store.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_DB = path.join(__dirname, '..', 'attribution.json');

function run() {
  const dbPath = process.env.NF_ATTRIBUTION_DB || DEFAULT_DB;
  if (!fs.existsSync(dbPath)) {
    console.log(`No attribution data yet at ${dbPath}.`);
    console.log('Generate token links and let leads visit before exporting.');
    return;
  }
  const store = new AttributionStore(dbPath);
  const args = process.argv.slice(2);
  const csvIdx = args.indexOf('--csv');
  const csvFlag = csvIdx !== -1;
  const outDir = csvFlag ? args[csvIdx + 1] : 'out';

  const rows = store.allVisits();
  const summary = store.summary();

  if (!csvFlag) {
    console.log(`Total attributed visits: ${rows.length}`);
    console.table(summary);
  } else {
    fs.mkdirSync(outDir, { recursive: true });
    const visitsPath = path.join(outDir, 'visits.csv');
    const summaryPath = path.join(outDir, 'summary.csv');
    fs.writeFileSync(visitsPath, csv(rows, ['id','lead_id','campaign','link_ref','visited_at','path','referrer']));
    fs.writeFileSync(summaryPath, csv(summary, ['lead_id','visit_count','first_visit','last_visit','clicked']));
    console.log(`Wrote ${visitsPath} (${rows.length} visits)`);
    console.log(`Wrote ${summaryPath} (${summary.length} leads)`);
  }
  store.close();
}

function csv(rows, cols) {
  const esc = (v) => `"${String(v == null ? '' : v).replaceAll('"', '""')}"`;
  const header = cols.map(esc).join(',');
  const lines = rows.map((r) => cols.map((c) => esc(r[c])).join(','));
  return [header, ...lines].join('\n');
}

run();