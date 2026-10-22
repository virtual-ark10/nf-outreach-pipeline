// autolink_resolve_exact_test.cjs — a bullet must resolve to the publication it names.
//
// Two failures this pins, both seen in the live queue on 2026-10-13:
//
//   1. The search endpoint never returns some real publications ('The AI Corner',
//      'Interconnects AI', 'Discover AI', 'AI Futures Project', 'AI Action Letter' are all
//      absent from /search results while sitting in the corpus with their own subscriber
//      labels). A bullet naming one kept its [TRACKED_LINK] placeholder, and the send gate
//      refuses a draft that still has one. The resolver now falls back to
//      /publications?q= — which does match on the publication's own name.
//   2. A publication whose name normalizes to the empty string (a non-Latin script) used to
//      score 80 against ANY query, because every string "includes" the empty needle. It took
//      the mint, so 'This is For Real.' was linked to an unrelated Arabic-language newsletter.
//      An empty normalized name must never match.
//
//   node tests/autolink_resolve_exact_test.cjs
'use strict';

const http = require('http');

const autoLinks = require('../outreach-autolinks.cjs');

let searchCalls = 0;
let publicationCalls = 0;

const send = (res, code, body) => {
  res.writeHead(code, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(body));
};

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  const q = decodeURIComponent(url.searchParams.get('q') || '');
  const isPublications = url.pathname.endsWith('/publications');
  if (isPublications) {
    publicationCalls += 1;
    if (/ai corner/i.test(q)) {
      return send(res, 200, { data: [{ name: 'The AI Corner', slug: 'theaicorner1', subscribersLabel: '151K' }] });
    }
    // Anything else the name endpoint cannot place either.
    return send(res, 200, { data: [] });
  }
  searchCalls += 1;
  // /search answers a topic-shaped result set that buries the exact publication, and returns
  // a non-Latin name for an unrelated query — the row that used to win on the empty needle.
  if (/ghost/i.test(q)) {
    return send(res, 200, { data: { newsletters: [{ name: '\u0646\u0634\u0631\u0629 \u0627\u0644\u062a\u0646\u0641\u064a\u0630\u064a\u064a\u0646', slug: 'amtar' }] } });
  }
  if (/good pub/i.test(q)) {
    return send(res, 200, { data: { newsletters: [{ name: 'Good Pub', slug: 'good-pub' }] } });
  }
  return send(res, 200, { data: { newsletters: [] } });
});

const results = [];
function check(name, ok, detail) {
  results.push({ name, ok });
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name}${ok || !detail ? '' : ` — ${detail}`}`);
}

server.listen(0, '127.0.0.1', async () => {
  const cfg = { apiBase: `http://127.0.0.1:${server.address().port}/api/v1`, token: 't0ken' };
  try {
    // 1. search misses it, the name endpoint has it: the bullet still resolves, by slug.
    autoLinks.resetResolveBudget();
    const corner = await autoLinks.resolvePublication('The AI Corner', cfg);
    check('a publication /search cannot find resolves via /publications', !!corner && corner.slug === 'theaicorner1',
      JSON.stringify(corner));
    check('the name endpoint was actually asked', publicationCalls > 0, `publicationCalls=${publicationCalls}`);

    // 2. a name search already resolves exactly is answered as before, and not re-asked.
    autoLinks.resetResolveBudget();
    const beforePub = publicationCalls;
    const good = await autoLinks.resolvePublication('Good Pub', cfg);
    check('an exact search hit still resolves', !!good && good.slug === 'good-pub', JSON.stringify(good));
    check('an exact search hit does not fall through to the name endpoint',
      publicationCalls === beforePub, `publicationCalls=${publicationCalls - beforePub}`);

    // 3. a non-Latin (empty-normalizing) row never wins a query it does not match.
    autoLinks.resetResolveBudget();
    const ghost = await autoLinks.resolvePublication('Ghost Writer Weekly', cfg);
    check('a name that normalizes to empty never matches', ghost === null, JSON.stringify(ghost));
  } catch (e) {
    check('test ran without throwing', false, e && e.stack);
  } finally {
    server.close();
    const failed = results.filter((r) => !r.ok);
    console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
    process.exit(failed.length ? 1 : 0);
  }
});
