#!/usr/bin/env node
/**
 * Chart test for the pad's Tracking tab. No browser can run on this box (no
 * browser binary, no libnss3/libasound, no passwordless sudo, no node-canvas),
 * so this is the strongest check available: it loads the SAME vendored Chart.js
 * the browser would load, drives the pad's OWN render path against the REAL
 * /api/tracking endpoint, and asserts that every chart is constructed and that
 * the context actually receives drawing calls.
 *
 * What this does NOT prove: pixel output. Only a real browser can speak to that
 * (see the resend-pad skill's note about client verification).
 *
 * Usage: node tests/pad_tracking_charts_test.cjs
 */
'use strict';
const fs = require('fs');
const path = require('path');

let TOKEN = '';
for (const line of fs.readFileSync(path.join(__dirname, '..', '.env'), 'utf8').split('\n')) {
  if (line.startsWith('PAD_TOKEN=')) TOKEN = line.slice('PAD_TOKEN='.length).trim();
}
if (!TOKEN) { console.error('no PAD_TOKEN'); process.exit(2); }

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const m = html.match(/<script>\s*"use strict";([\s\S]*?)<\/script>/);
if (!m) { console.error('no inline script found'); process.exit(2); }
const clientSrc = m[1];

// ---------------------------------------------------------------- 2D context
// A recording context: every method call is counted, so "the chart drew" is an
// observation rather than an assumption.
const drawCalls = [];
function makeCtx(canvasEl) {
  const props = {};
  const special = {
    canvas: canvasEl,
    measureText: (t) => ({ width: String(t).length * 6, actualBoundingBoxAscent: 8, actualBoundingBoxDescent: 2 }),
    createLinearGradient: () => ({ addColorStop() {} }),
    createRadialGradient: () => ({ addColorStop() {} }),
    createPattern: () => ({}),
    getImageData: () => ({ data: new Uint8ClampedArray(4), width: 1, height: 1 }),
    isPointInPath: () => false,
    getLineDash: () => [],
  };
  const settable = /^(canvas|font|fillStyle|strokeStyle|lineWidth|lineCap|lineJoin|miterLimit|lineDashOffset|textAlign|textBaseline|globalAlpha|globalCompositeOperation|shadowBlur|shadowColor|shadowOffsetX|shadowOffsetY|direction|filter|imageSmoothingEnabled|letterSpacing|wordSpacing|fontKerning|textRendering)$/;
  return new Proxy(props, {
    get(t, k) {
      if (k in special) return special[k];
      if (typeof k === 'string' && settable.test(k)) return t[k];
      return (...a) => { drawCalls.push(String(k)); return undefined; };
    },
    set(t, k, v) { t[k] = v; return true; },
  });
}

// ---------------------------------------------------------------- DOM stub
function makeEl(id) {
  const cls = new Set();
  const el = {
    id, innerHTML: '', textContent: '', value: '', style: {}, dataset: {}, files: [],
    width: 600, height: 260, tagName: 'CANVAS',
    classList: { add: (c) => cls.add(c), remove: (c) => cls.delete(c), contains: (c) => cls.has(c), toggle: () => {} },
    addEventListener() {}, removeEventListener() {}, insertAdjacentHTML() {}, appendChild() {}, remove() {},
    focus() {}, click() {}, getAttribute: () => null, setAttribute() {}, hasAttribute: () => false,
    getBoundingClientRect: () => ({ width: 600, height: 260, top: 0, left: 0, right: 600, bottom: 260 }),
    parentNode: null, isConnected: true,
  };
  el.getContext = () => (el._ctx = el._ctx || makeCtx(el));
  return el;
}
const nodes = {};
const htmlEl = makeEl('html');
htmlEl.isConnected = true;
const doc = {
  getElementById: (id) => (nodes[id] = nodes[id] || makeEl(id)),
  querySelectorAll: () => [], querySelector: () => null,
  createElement: (t) => makeEl('new-' + t), addEventListener() {}, body: makeEl('body'),
  documentElement: htmlEl,
};
// Chart.js measures through el.ownerDocument.defaultView.getComputedStyle(el) and
// only calls update() (i.e. actually draws) when the canvas is attached.
doc.defaultView = global;
htmlEl.ownerDocument = doc; htmlEl.parentNode = doc.body; doc.body.ownerDocument = doc;
const wrap = (fn, extra) => (arg) => { const el = fn(arg); el.ownerDocument = doc; if (extra) extra(el); return el; };
doc.getElementById = wrap(doc.getElementById, (el) => { el.parentNode = htmlEl; });
doc.createElement = wrap(doc.createElement);
const store = {};
global.document = doc;
global.window = global;
global.self = global;
global.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
global.sessionStorage = global.localStorage;
global.getComputedStyle = () => ({ getPropertyValue: () => '', width: '600px', height: '260px' });
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
global.MutationObserver = class { constructor(cb) { this.cb = cb; } observe() {} disconnect() {} takeRecords() { return []; } };
global.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
global.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 0);
global.cancelAnimationFrame = (h) => clearTimeout(h);
// Chart.js registers its resize listener on the window; a browser has these, the
// node global does not, so the stub provides no-ops.
global.addEventListener = () => {};
global.removeEventListener = () => {};
global.devicePixelRatio = 1;
global.alert = () => {};
try { Object.defineProperty(global, 'navigator', { value: { userAgent: 'node', clipboard: { writeText: async () => {} } }, configurable: true }); } catch (e) { /* keep node's */ }

const realFetch = global.fetch;
global.fetch = (url, opts) => {
  const abs = url.startsWith('http') ? url : 'http://127.0.0.1:3001/' + url.replace(/^\//, '');
  return realFetch(abs, opts).then(async (r) => {
    const text = await r.text();
    return { ok: r.ok, status: r.status, text: async () => text, json: async () => JSON.parse(text) };
  });
};

// ---------------------------------------------------------------- Chart.js
const ChartMod = require(path.join(__dirname, '..', 'vendor', 'chart.umd.min.js'));
const Chart = ChartMod.Chart || ChartMod;
if (typeof Chart !== 'function') { console.error('vendored Chart.js did not export a constructor'); process.exit(2); }

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
global.localStorage.setItem('nf_pad_token', TOKEN);

(async () => {
  // eslint-disable-next-line no-eval
  eval(clientSrc + '\n;globalThis.__pad = { connectPad: connectPad, loadTracking: loadTracking, liveCharts: liveCharts, makeChart: makeChart };');
  const P = globalThis.__pad;
  await P.connectPad(TOKEN);
  await wait(800);

  const checks = [];
  const has = (label, ok) => checks.push([label, !!ok]);

  has('vendored Chart.js loads and exports a constructor', typeof Chart === 'function');
  console.log('  Chart.js version:', Chart.version || '(unknown)');
  has('the client can see a real canvas context', typeof doc.getElementById('ch_activity').getContext === 'function');

  await P.loadTracking();           // drives the real /api/tracking endpoint
  await wait(2000);                 // Chart.js renders on animation frames

  const ids = ['ch_activity', 'ch_status', 'ch_funnel', 'ch_leads', 'ch_campaigns', 'ch_errors'];
  const made = P.liveCharts || {};
  for (const id of ids) has('chart constructed: ' + id, !!made[id]);
  has('all six charts constructed', ids.every((id) => !!made[id]));
  has('drawing calls reached the canvas context', drawCalls.length > 50);

  const act = made.ch_activity;
  if (act) {
    has('activity chart is a line chart', act.config.type === 'line');
    has('activity chart has 4 datasets (sent/replies/clicks/failures)', act.config.data.datasets.length === 4);
    console.log('  activity datasets:', act.config.data.datasets.map((d) => d.label + '=' + d.data.length + 'pts').join(', '));
    has('activity chart has day labels', act.config.data.labels.length > 0);
  }
  const fun = made.ch_funnel;
  if (fun) {
    has('funnel chart is a horizontal bar', fun.config.type === 'bar' && fun.config.options.indexAxis === 'y');
    has('funnel has the 5 engagement stages', fun.config.data.labels.join(',') === 'Sent,Delivered,Opened,Clicked,Replied');
  }
  const errs = made.ch_errors;
  if (errs) has('failures chart is present with a labelled axis', errs.config.type === 'bar' && errs.config.data.labels.length > 0);

  let pass = 0;
  for (const [label, ok] of checks) { console.log((ok ? '  PASS  ' : '  FAIL  ') + label); if (ok) pass++; }
  console.log('\n  context draw calls recorded:', drawCalls.length);
  console.log(`${pass}/${checks.length} checks passed`);
  process.exit(pass === checks.length ? 0 : 1);
})().catch((e) => { console.error('harness error:', e && (e.stack || e.message)); process.exit(2); });
