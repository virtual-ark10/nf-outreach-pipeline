#!/usr/bin/env node
// Rebuild links.csv from the attribution store (attribution.json).
//
// WHY: `utils/generate-links.js` OVERWRITES links.csv on every run, so the CSV
// only ever holds the last run's rows. The store is the complete ledger of
// every token ever minted (key `clicks`) — read from it to get a full-batch
// export without minting new tokens.
//
// Emitted links are TOKEN-ONLY (no UTM, no dest param) — destination resolves
// server-side from the token record.
//
// Usage:
//   node scripts/rebuild-links.js                # all campaigns
//   node scripts/rebuild-links.js sep2-2026      # one campaign
// Env: NF_ATTRIBUTION_DB (default ../attribution.json),
//      NF_BASE_URL (default https://newsletterfit.com)
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DB_PATH = process.env.NF_ATTRIBUTION_DB || path.join(__dirname, '..', 'attribution.json');
const BASE_URL = process.env.NF_BASE_URL || 'https://newsletterfit.com';
const campaign = process.argv[2] || null;

const db = JSON.parse(fs.readFileSync(DB_PATH, 'utf8'));
const rows = (db.clicks || [])
  .filter((t) => t && t.token)
  .filter((t) => !campaign || t.campaign === campaign)
  .map((t) => [t.lead_id, t.campaign, t.link_ref, t.dest, `${BASE_URL}/api/click?lt=${t.token}`]);

const esc = (v) => `"${String(v ?? '').replaceAll('"', '""')}"`;
const csv = ['lead_id,campaign,type,dest,link']
  .concat(rows.map((r) => r.map(esc).join(',')))
  .join('\n');

const outPath = path.join(__dirname, '..', 'links.csv');
fs.writeFileSync(outPath, csv + '\n');
console.log(`Wrote ${rows.length} token-only link row(s) to ${outPath} (from ${DB_PATH})`);