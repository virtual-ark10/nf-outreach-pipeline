// doc_links_test.cjs — [DOCS_LINK] and [MCP_LINK] mint a token for a fixed page.
//
// The rule: wherever a draft names the REST docs or the MCP endpoint, the click should be
// attributable, and the plain-text part must still read as a URL a person can type. So the
// placeholder becomes a readable URL in text and a token anchor in html, and a draft that could
// not be minted keeps its placeholder so the send path refuses it instead of shipping "[DOCS_LINK]".
//
//   node tests/doc_links_test.cjs
'use strict';

const http = require('http');
const os = require('os');
const path = require('path');

const autoLinks = require('../outreach-autolinks.cjs');

const minted = [];
const send = (res, code, body) => {
  res.writeHead(code, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(body));
};

const server = http.createServer((req, res) => {
  if (req.method === 'POST' && req.url.includes('/outreach/links')) {
    let raw = '';
    req.on('data', (c) => { raw += c; });
    req.on('end', () => {
      const body = JSON.parse(raw || '{}');
      const token = `tok${minted.length}`.padEnd(32, 'x');
      minted.push({ dest: body.dest, ref: body.ref, token });
      send(res, 201, { data: { token, dest: body.dest, ref: body.ref } });
    });
    return;
  }
  send(res, 200, { data: { newsletters: [] } });
});

const results = [];
function check(name, ok, detail) {
  results.push({ name, ok });
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name}${ok || !detail ? '' : ` — ${detail}`}`);
}

server.listen(0, '127.0.0.1', async () => {
  const apiBase = `http://127.0.0.1:${server.address().port}/api/v1`;
  const storePath = path.join(os.tmpdir(), `nf-doclinks-${process.pid}.json`);
  const cfg = { apiBase, token: 't0ken', baseUrl: 'https://newsletterfit.com', storePath };
  try {
    const draft = {
      id: 'example-draft',
      leadId: 'example',
      from: 'Ian Hinga <hi@newsletterfit.com>',
      campaign: 'test',
      text: 'Hi Jan,\n\nThe docs sit at [DOCS_LINK] and the agent endpoint at [MCP_LINK].\n\n'
        + 'If a key is easier, ask and I will send one.',
    };

    const out = await autoLinks.prepareDraft(draft, cfg);
    check('the docs placeholder becomes a readable URL in text',
      out.text.includes('newsletterfit.com/docs/api') && !out.text.includes('[DOCS_LINK]'), out.text);
    check('the MCP placeholder becomes a readable URL in text',
      out.text.includes('newsletterfit.com/mcp') && !out.text.includes('[MCP_LINK]'), out.text);
    check('no tracking token leaks into the plain-text part', !/lt=/.test(out.text), out.text);
    check('both pages were minted', minted.filter((m) => /\/docs\/api$|\/mcp$/.test(m.dest)).length === 2,
      JSON.stringify(minted));
    check('html anchors the docs URL with the token',
      /<a href="https:\/\/newsletterfit\.com\/api\/click\?lt=[A-Za-z0-9_-]+">newsletterfit\.com\/docs\/api<\/a>/.test(out.html),
      out.html);
    check('html anchors the MCP URL with the token',
      /<a href="https:\/\/newsletterfit\.com\/api\/click\?lt=[A-Za-z0-9_-]+">newsletterfit\.com\/mcp<\/a>/.test(out.html),
      out.html);
    check('nothing is left unfilled', autoLinks.unfilled(out.text, out.html).length === 0,
      JSON.stringify(autoLinks.unfilled(out.text, out.html)));

    // No token on the pad: the placeholder must survive so the send path refuses the draft.
    const blind = await autoLinks.prepareDraft(draft, { ...cfg, token: null });
    check('without a token the placeholder survives',
      blind.text.includes('[DOCS_LINK]'), blind.text);
    check('and the send path sees it as unfilled',
      autoLinks.unfilled(blind.text, blind.html).includes('[DOCS_LINK]'),
      JSON.stringify(autoLinks.unfilled(blind.text, blind.html)));
  } catch (e) {
    check('test ran without throwing', false, e && e.stack);
  } finally {
    server.close();
    try { require('fs').unlinkSync(storePath); } catch (e) { /* not written */ }
    const failed = results.filter((r) => !r.ok);
    console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
    process.exit(failed.length ? 1 : 0);
  }
});
