// Generate token-tagged links to drop into outreach emails.
//
// The point of this version: every suggested pub or article becomes a REAL
// tracked link, so a click tells you WHICH lead clicked WHICH suggestion.
//
// Input: a leads CSV with columns:
//   lead_id, campaign, dest1, ref1, dest2, ref2, ...   (destN = real URL the
//                                                       link lands on; refN =
//                                                       label like "pub-migma")
// Example leads.csv:
//   lead_123,campaign-1,https://migma.io,map succes,pub-migma,https://pub2.com/recap,article-recap
//
// Usage:
//   node utils/generate-links.js leads.csv     -> writes links.csv
//   node utils/generate-links.js --lead lead_123 --campaign c --dest https://x.com --ref pub-x
//
// links.csv columns: lead_id, campaign, type, dest, link
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { AttributionStore } from '../src/store.js';
import { generateToken, buildTokenLink } from '../src/token.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE_URL = process.env.NF_BASE_URL || 'https://newsletterfit.com';

const args = process.argv.slice(2);
const store = new AttributionStore(process.env.NF_ATTRIBUTION_DB || path.join(__dirname, '..', 'attribution.json'));

// Collect (lead_id, campaign, linkRef, dest) tuples.
const links = [];

if (args.includes('--lead')) {
  const leadId = args[args.indexOf('--lead') + 1];
  const campaign = (args.indexOf('--campaign') !== -1) ? args[args.indexOf('--campaign') + 1] : 'default';
  const dest = (args.indexOf('--dest') !== -1) ? args[args.indexOf('--dest') + 1] : '/';
  const ref = (args.indexOf('--ref') !== -1) ? args[args.indexOf('--ref') + 1] : 'main';
  links.push({ lead_id: leadId, campaign, ref, dest });
} else if (args[0] && fs.existsSync(args[0])) {
  const lines = fs.readFileSync(args[0], 'utf8').split('\n').slice(1);
  for (const line of lines) {
    if (!line.trim()) continue;
    const cells = line.split(',').map((s) => s.trim());
    const [leadId, campaign = 'default'] = cells;
    if (!leadId) continue;
    // pair up destN,refN columns
    for (let i = 2; i + 1 < cells.length; i += 2) {
      const dest = cells[i];
      const ref = cells[i + 1] || `link${(i - 2) / 2}`;
      if (dest) links.push({ lead_id: leadId, campaign, ref, dest });
    }
  }
} else {
  console.log('Pass a leads CSV (lead_id,campaign,dest1,ref1,dest2,ref2,...) or --lead ID --campaign C --dest URL --ref NAME');
  process.exit(1);
}

const out = [];
for (const { lead_id, campaign, ref, dest } of links) {
  const tok = generateToken(lead_id, campaign, ref, dest, 30);
  store.addToken(tok);
  // Link carries ONLY the tracking token; destination resolved server-side from the
  // token record. No UTM params in the emailed URL (keeps links short + clean).
  const link = buildTokenLink(BASE_URL, tok);
  out.push({ lead_id, campaign, type: ref, dest, link });
  console.log(`lead=${lead_id} ref=${ref} -> ${dest}\n  ${link}\n`);
}

// Only overwrite links.csv if we actually generated links.
if (out.length) {
  fs.writeFileSync(path.join(__dirname, '..', 'links.csv'), csv(out, ['lead_id','campaign','type','dest','link']));
}
console.log(`Wrote links.csv (${out.length} tracked links); tokens saved to attribution.json`);

function csv(rows, cols) {
  const esc = (v) => `"${String(v ?? '').replaceAll('"', '""')}"`;
  return [cols.map(esc).join(',')].concat(rows.map((r) => cols.map((c) => esc(r[c])).join(','))).join('\n');
}
store.close();