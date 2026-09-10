// Smoke test: exercises the full flow against a throwaway SQLite DB.
//   token issued -> /api/click (via a fake request) -> attribution cookie
//   -> repeated visits recorded -> summary reflects the lead.
import { rmSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { AttributionStore } from '../src/store.js';
import { generateToken, buildTokenLink, resolveToken } from '../src/token.js';
import { clickRouter, trackVisitMiddleware, parseCookies } from '../src/middleware.js';

const dbPath = fileURLToPath(new URL('./smoke-demo.json', import.meta.url));
try { rmSync(dbPath, { force: true }); } catch {}
const store = new AttributionStore(dbPath);
let passed = 0;
const assert = (cond, msg) => {
  if (!cond) throw new Error('FAIL: ' + msg);
  passed++;
  console.log('  ok -', msg);
};

// 1) Issue a token for a lead: which suggestion it guards (linkRef) + where it
//    redirects to (dest). dest is kept SERVER-SIDE, not in the email link.
const tok = generateToken('lead_123', 'campaign-1', 'pub-migma', '/pricing');
store.addToken(tok);
const link = buildTokenLink('https://newsletterfit.com', tok, {
  utm_source: 'outlook', utm_medium: 'email', utm_campaign: 'campaign-1',
});
console.log('Token link:', link);
assert(link.includes('/api/click?lt='), 'link points at /api/click with token');
assert(link.includes('utm_campaign=campaign-1'), 'UTM carried into link');
assert(!link.includes('dest='), 'link does NOT expose the external destination URL');

// 2) Simulate the click request through clickRouter.
const rec = resolveToken(store, tok.token);
assert(rec && rec.lead_id === 'lead_123', 'token resolves to lead_123');
assert(store.summary().length === 0, 'no visits recorded yet (only a token issued)');

function fakeClick(query, allowed = null) {
  const setHeader = [];
  const res = {
    status: (code) => (res.statusCode = code, res),
    send: (m) => (res.body = m, res),
    append: (k, v) => setHeader.push([k, v]),
    redirect: (code, url) => (res.redir = { code, url }, res),
    statusCode: 200, redir: null,
  };
  clickRouter(store, { cookieMaxDays: 30, allowedDestinations: allowed })({ query, get: () => 'newsletterfit.com', protocol: 'https' }, res);
  return { res, setHeader };
}
// The dest query param is IGNORED — server resolves from the token record (here /pricing).
const { res, setHeader } = fakeClick({ lt: tok.token, utm_source: 'outlook' });
assert(res.redir.code === 302, 'click 302-redirects to destination');
assert(res.redir.url.includes('/pricing'), 'redirect went to server-side dest (token record), not query');
assert(res.statusCode === 200, 'no errored status');
const cookieLine = setHeader.find(([k]) => k === 'Set-Cookie');
assert(cookieLine && cookieLine[1].includes('nf_lead='), 'attribution cookie set on click');

// Allowlist blocks a destination outside the allowed domains.
const extTok = generateToken('lead_777', 'campaign-1', 'pub-ext', 'https://migma.io');
store.addToken(extTok);
const blocked = fakeClick({ lt: extTok.token, utm_source: 'outlook' }, ['newsletterfit.com']);
assert(blocked.res.statusCode === 403, 'allowlist blocks redirects to domains outside allowlist');
// ...but allows the same token through when its domain is listed.
const allowedClick = fakeClick({ lt: extTok.token }, ['newsletterfit.com', 'migma.io']);
assert(allowedClick.res.redir && allowedClick.res.redir.code === 302, 'allowlist passes destination for a listed domain');
assert(allowedClick.res.redir.url.includes('migma.io'), 'redirect target resolved from server-side token record');
// click recorded (first_click_at now set)
assert(store.lookupToken(tok.token).first_click_at, 'click recorded on the token');
// NO visit yet — visits come from the page middleware, not the click.
assert(store.allVisits().length === 0, 'click alone does not count as a visit');

// 3) Simulate repeat site visits via trackVisitMiddleware with the cookie.
const cookieHeader = cookieLine[1].split(';')[0];
function fakePageReq(url) {
  return {
    headers: { cookie: cookieHeader },
    originalUrl: url,
    get: () => '',
  };
}
const mw = trackVisitMiddleware(store);
mw(fakePageReq('/pricing'), {}, () => {});
mw(fakePageReq('/pricing'), {}, () => {});
mw(fakePageReq('/blog'), {}, () => {});
assert(store.allVisits().length === 3, '3 attributed visits recorded from cookie');
assert(store.visitsForLead('lead_123').length === 3, 'visits grouped by lead');
// linkRef must flow from token -> cookie -> visit
assert(store.allVisits().every((v) => v.link_ref === 'pub-migma'), 'link_ref (which suggestion) recorded on visits');

// 4) Expired / unknown token rejected.
assert(resolveToken(store, 'nope') === null, 'unknown token rejected');
const stale = generateToken('lead_9', 'x', -1); // ttlDays=-1 => already expired
console.log('  noting expired token resolves null ->', resolveToken(store, stale.token) === null);
passed++;

// 5) Summary shape.
const s = store.summary();
assert(s[0].lead_id === 'lead_123', 'summary has lead_123');
assert(s[0].visit_count === 3, 'summary visit_count=3');
console.log('\nSummary:', JSON.stringify(s, null, 2));
console.log(`\nALL ${passed} CHECKS PASSED ✔`);
store.close();