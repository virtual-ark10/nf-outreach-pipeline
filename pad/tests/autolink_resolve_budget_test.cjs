// autolink_resolve_budget_test.cjs — the link pass must not eat the corpus API's rate limit.
//
// Background: the pad's link sweep re-reads the whole queue every couple of minutes, and the
// corpus API allows 90 requests/min for everything on this host. Resolving every publication
// name on every sweep emptied that bucket, so nothing resolved and all 90 queued drafts sat
// at the send gate with `0/90 filled, 0 link(s) minted` on every cycle (2026-10-12).
//
// What this pins, against a local stub of the search endpoint:
//   1. a name is resolved once and then served from the cache (no request on the second ask),
//   2. a 429 is NOT retried into: one request, pass stops, cooldown set,
//   3. the per-pass call budget is respected, and a pass cut short remembers nothing,
//   4. after the cooldown the next pass asks again (nothing is locked out).
//
//   node tests/autolink_resolve_budget_test.cjs
'use strict';

const http = require('http');

process.env.LINK_RESOLVE_MAX_CALLS = '3';
process.env.LINK_RESOLVE_TTL_MS = '60000';
process.env.LINK_RESOLVE_MISS_TTL_MS = '60000';
process.env.LINK_RESOLVE_COOLDOWN_MS = '400';

const autoLinks = require('../outreach-autolinks.cjs');

let requests = 0;
let mode = 'ok';

const server = http.createServer((req, res) => {
  requests += 1;
  const q = decodeURIComponent((req.url.split('q=')[1] || '')).toLowerCase();
  const send = (code, body) => {
    res.writeHead(code, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(body));
  };
  if (q.includes('rate')) return send(429, { message: 'Too many requests' });
  if (q.includes('good')) {
    return send(200, { data: { newsletters: [{ name: 'Good Pub', slug: 'good-pub' }] } });
  }
  return send(200, { data: { newsletters: [] } });
});

const results = [];
function check(name, ok, detail) {
  results.push({ name, ok, detail });
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name}${ok || !detail ? '' : ` — ${detail}`}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

server.listen(0, '127.0.0.1', async () => {
  const cfg = { apiBase: `http://127.0.0.1:${server.address().port}/api/v1`, token: 't0ken' };
  try {
    // 1. resolve once, then from the cache
    autoLinks.resetResolveBudget();
    const before = requests;
    const first = await autoLinks.resolvePublication('Good Pub', cfg);
    const afterFirst = requests;
    check('a good name resolves to its slug', !!first && first.slug === 'good-pub', JSON.stringify(first));
    check('the first ask hits the API once', afterFirst - before === 1, `calls=${afterFirst - before}`);

    const second = await autoLinks.resolvePublication('Good Pub', cfg);
    check('the second ask is cached (no extra request)', requests === afterFirst && !!second && second.slug === 'good-pub',
      `calls=${requests - afterFirst}`);

    // 2. a 429 is not retried into, and it puts the pass on cooldown
    autoLinks.resetResolveBudget();
    const beforeRate = requests;
    const rate = await autoLinks.resolvePublication('Rate Pub', cfg);
    const rateCalls = requests - beforeRate;
    check('a 429 returns no match', rate === null, JSON.stringify(rate));
    check('a 429 is sent ONCE, not retried 3x', rateCalls === 1, `calls=${rateCalls}`);

    const duringCooldown = requests;
    const other = await autoLinks.resolvePublication('Some Other Pub', cfg);
    check('during the cooldown nothing is asked at all', other === null && requests === duringCooldown,
      `calls=${requests - duringCooldown}`);
    check('a name cut short by the cooldown is NOT cached as a miss',
      await (async () => {
        await sleep(450);                    // cooldown expires
        const beforeRetry = requests;
        const again = await autoLinks.resolvePublication('Good Pub', cfg);
        return !!again && again.slug === 'good-pub' && requests === beforeRetry;
      })(), 'good name still answers from cache after the cooldown');

    // 3. the per-pass budget caps the calls, and the pass cuts off cleanly
    const calls = [];
    autoLinks.resetResolveBudget();
    const beforeBudget = requests;
    for (const n of ['Miss One', 'Miss Two', 'Miss Three', 'Miss Four']) {
      calls.push(await autoLinks.resolvePublication(n, cfg));
    }
    check('the pass spends no more than LINK_RESOLVE_MAX_CALLS', requests - beforeBudget <= 3,
      `calls=${requests - beforeBudget}`);
    check('unresolved names return null, not a throw', calls.every((c) => c === null), JSON.stringify(calls));
  } catch (e) {
    check('test ran without throwing', false, e && e.stack);
  } finally {
    server.close();
    const failed = results.filter((r) => !r.ok);
    console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
    process.exit(failed.length ? 1 : 0);
  }
});
