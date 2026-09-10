#!/usr/bin/env node
/**
 * Render test for the pad's Leads (CRM) tab. No browser exists on this box, so
 * this drives the pad's OWN inline script in a DOM stub with fetch pointed at
 * the real local pad API (which proxies the leads engine). It is an integration
 * test of the render path + the one-token proxy, not a claim about pixels.
 *
 * Usage: node pad_crm_render_test.cjs
 */
'use strict';
const fs = require('fs');
const path = require('path');

let TOKEN = '';
// read the pad's token from this checkout's .env (path derived, not hardcoded)
for (const line of fs.readFileSync(path.join(__dirname, '..', '.env'), 'utf8').split('\n')) {
  if (line.startsWith('PAD_TOKEN=')) TOKEN = line.slice('PAD_TOKEN='.length).trim();
}
if (!TOKEN) { console.error('no PAD_TOKEN'); process.exit(2); }

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const m = html.match(/<script>\s*"use strict";([\s\S]*?)<\/script>/);
if (!m) { console.error('no inline script found'); process.exit(2); }
const clientSrc = m[1];

// ---------------------------------------------------------------- DOM stub
function makeEl(id) {
  const cls = new Set();
  return {
    id, innerHTML: '', textContent: '', value: '', style: {}, files: [], dataset: {},
    classList: {
      add: (c) => cls.add(c), remove: (c) => cls.delete(c),
      contains: (c) => cls.has(c), toggle: (c) => (cls.has(c) ? cls.delete(c) : cls.add(c)),
    },
    addEventListener() {}, insertAdjacentHTML(pos, s) { this._html = (this._html || '') + s; },
    querySelectorAll: () => [], querySelector: () => null, appendChild() {}, remove() {},
    focus() {}, click() {},
  };
}
const nodes = {};
const doc = {
  getElementById: (id) => (nodes[id] = nodes[id] || makeEl(id)),
  querySelectorAll: () => [],
  querySelector: () => null,
  createElement: () => makeEl('new'),
  addEventListener() {},
  body: makeEl('body'),
};
const store = {};
global.document = doc;
global.window = global;
global.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
global.sessionStorage = global.localStorage;
try { Object.defineProperty(global, 'navigator', { value: { clipboard: { writeText: async () => {} } }, configurable: true }); } catch (e) { /* node exposes navigator as a getter; ignore */ }
global.alert = () => {};

const realFetch = global.fetch;
global.fetch = (url, opts) => {
  const abs = url.startsWith('http') ? url : 'http://127.0.0.1:3001/' + url.replace(/^\//, '');
  return realFetch(abs, opts).then(async (r) => {
    const text = await r.text();
    return { ok: r.ok, status: r.status, text: async () => text, json: async () => JSON.parse(text) };
  });
};

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
global.localStorage.setItem('nf_pad_token', TOKEN);

(async () => {
  // expose the pad's own functions to this harness
  // eslint-disable-next-line no-eval
  eval(clientSrc + '\n;globalThis.__pad = { connectPad: connectPad, loadCRM: loadCRM, crm: crm, switchTab: switchTab, crmSelect: crmSelect, loadTracking: loadTracking, renderAnalytics: renderAnalytics };');
  const P = globalThis.__pad;
  await P.connectPad(TOKEN);
  await wait(1500);

  const checks = [];
  const has = (label, cond) => checks.push([label, !!cond]);

  const list = (nodes.crm_list && nodes.crm_list.innerHTML) || '';
  const chips = (nodes.crm_chips && nodes.crm_chips.innerHTML) || '';
  const counter = (nodes.crm_count && nodes.crm_count.textContent) || '';

  has('leads table rendered', list.includes('<table class="leads"') && /class="row/.test(list));
  const rows = (list.match(/class="row/g) || []).length;
  console.log('  rendered lead rows:', rows);
  has('all leads rendered (>=15)', rows >= 15);
  has('stage pills coloured', /class="spill" style="background:#(64748b|2563eb|0d9488)/.test(list));
  has('role badges present', /class="role/.test(list));
  has('next-due column present', list.includes('Next due'));
  has('filter chips rendered', chips.includes('chipf'));
  has('sidebar counter filled', String(counter).length > 0);
  console.log('  sidebar counter:', counter);

  // select a lead -> detail panel
  P.crmSelect('brex');
  await wait(2500);
  const det = (nodes.crm_detail && nodes.crm_detail.innerHTML) || '';

  console.log('\n  detail panel after selecting a lead:', det.length > 80 ? 'rendered' : 'EMPTY (' + det.length + ' chars)');
  has('lead detail renders', det.length > 80);

  // ---- tracking dashboard (drives /api/tracking through the real pad)
  await P.loadTracking();
  await wait(1200);
  const kpis = (nodes.tr_kpis && nodes.tr_kpis.innerHTML) || '';
  const trLeads = (nodes.tr_leads && nodes.tr_leads.innerHTML) || '';
  const trErrs = (nodes.tr_errors && nodes.tr_errors.innerHTML) || '';
  const trCharts = (nodes.tr_charts && nodes.tr_charts.innerHTML) || '';
  const trSources = (nodes.tr_sources && nodes.tr_sources.textContent) || '';

  has('tracking KPI tiles rendered', kpis.includes('class="kpi"') && kpis.includes('Sent') && kpis.includes('Delivered'));
  has('tracking KPI tiles include clicks + replies + failures', kpis.includes('Clicked') && kpis.includes('Replied') && kpis.includes('Failures logged'));
  has('per-lead tracking table rendered', trLeads.includes('<table class="small"') || trLeads.includes('No leads'));
  has('failure list rendered (table or the all-clear note)', trErrs.includes('<table class="small"') || trErrs.includes('Nothing has failed'));
  has('source line names the store + window', trSources.includes('window: last') && trSources.includes('source:'));
  console.log('  tracking source line:', trSources.slice(0, 120));
  // The DOM stub has no canvas: the tab must degrade, not crash.
  has('charts degrade gracefully without a canvas', trCharts.includes('Charts unavailable') || trCharts.length === 0);

  // ---- analytics tab (providers listed, nothing claimed as connected)
  P.renderAnalytics();
  const prov = (nodes.an_providers && nodes.an_providers.innerHTML) || '';
  has('analytics lists both providers', prov.includes('Google Analytics 4') && prov.includes('PostHog'));
  has('analytics claims nothing is connected', prov.includes('not connected') && !prov.includes('>connected<'));

  let pass = 0;
  for (const [label, ok] of checks) { console.log((ok ? '  PASS  ' : '  FAIL  ') + label); if (ok) pass++; }
  console.log(`\n${pass}/${checks.length} checks passed`);
  process.exit(pass === checks.length ? 0 : 1);
})();
