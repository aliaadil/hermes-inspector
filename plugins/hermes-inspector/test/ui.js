#!/usr/bin/env node
// UI smoke test for the dashboard.
//
// We don't have a headless browser in CI, so this test asserts what we CAN
// without one:
//   1. /, /app.js, /styles.css all return 200 from the plugin.
//   2. The HTML contains the structural elements the JS depends on (so a
//      markup regression breaks the test, not just the running app).
//   3. app.js is parseable JavaScript (Node's parser catches typos).
//   4. app.js wires up the four board columns + the docs pane + the top-bar
//      filters + the auto-refresh toggle — verified by string-grep on the
//      source, since we can't simulate a real DOM here.
//   5. The total public-bundle size is well under the 200KB budget.
//
// The browser-side behaviour (drag-and-drop, localStorage, expand) is covered
// indirectly by the grep assertions on app.js; a follow-up Playwright/Puppeteer
// pass would close that loop when a headless browser is available.

'use strict';

const fs = require('fs');
const fsp = require('fs/promises');
const path = require('path');
const http = require('http');
const vm = require('vm');

const plugin = require('../index');

let passed = 0, failed = 0;
function ok(label, cond) {
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${label}`);
  if (cond) passed++; else failed++;
}

function requestRaw(port, method, urlPath) {
  return new Promise((resolve, reject) => {
    const req = http.request({
      host: '127.0.0.1', port, method, path: urlPath,
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({
        status: res.statusCode,
        headers: res.headers,
        body: Buffer.concat(chunks),
      }));
    });
    req.on('error', reject);
    req.end();
  });
}

(async () => {
  const tmpRoot = path.join(__dirname, '..', 'data', `ui-${Date.now()}`);
  fs.mkdirSync(tmpRoot, { recursive: true });
  const dataPath = path.join(tmpRoot, 'inspector.db');

  // Seed a few rows so the JS loaders have something to chew on.
  const server = await plugin.runStandalone({ port: 0, data_path: dataPath, backend: 'sqlite' });
  await plugin.onEvent('task_created', { id: 't_ui_a', title: 'Alpha card', assignee: 'builder' });
  await plugin.onEvent('task_created', { id: 't_ui_b', title: 'Beta card',  assignee: 'reviewer' });
  await plugin.onEvent('task_started', { id: 't_ui_c', title: 'Gamma card', assignee: 'builder' });
  await plugin.onEvent('task_completed', { id: 't_ui_d', title: 'Delta card', assignee: 'builder' });
  await plugin.onEvent('doc_emitted', {
    id: 'doc_ui_1', task_id: 't_ui_a',
    title: 'Alpha doc',
    content: '# Alpha\n\nfirst paragraph\n\n```js\nconsole.log("hi")\n```',
    source: 'note',
  });
  await plugin.onEvent('doc_emitted', {
    id: 'doc_ui_2', task_id: 't_ui_d',
    title: 'Delta doc',
    content: 'plain text body',
    source: 'adr',
  });

  // ---- 1. Static assets. ----
  const idx = await requestRaw(server.port, 'GET', '/');
  ok('GET / returns 200', idx.status === 200);
  ok('GET / serves HTML', idx.headers['content-type'] && idx.headers['content-type'].includes('text/html'));

  const css = await requestRaw(server.port, 'GET', '/styles.css');
  ok('GET /styles.css returns 200', css.status === 200);

  const js = await requestRaw(server.port, 'GET', '/app.js');
  ok('GET /app.js returns 200', js.status === 200);

  // ---- 2. HTML structure. ----
  const html = idx.body.toString('utf8');
  ok('HTML has #board root',               /id="board"/.test(html));
  ok('HTML has #docs root',                /id="docs"/.test(html));
  ok('HTML has search filter',             /id="f-search"/.test(html));
  ok('HTML has date-from filter',          /id="f-date-from"/.test(html));
  ok('HTML has date-to filter',            /id="f-date-to"/.test(html));
  ok('HTML has source-task filter',        /id="f-source-task"/.test(html));
  ok('HTML has manual refresh button',     /id="btn-refresh"/.test(html));
  ok('HTML has auto-refresh toggle',       /id="f-auto"/.test(html));
  ok('HTML has status indicator',          /id="status"/.test(html));
  ok('HTML has toast container',           /id="toast"/.test(html));
  ok('HTML loads app.js defer',            /<script src="\/app.js" defer/.test(html));

  // ---- 3. JS parses cleanly. ----
  const jsSrc = js.body.toString('utf8');
  let parsed = false;
  try { new vm.Script(jsSrc, { filename: 'app.js' }); parsed = true; } catch (e) {
    console.error('  parse error:', e.message);
  }
  ok('app.js parses as valid JavaScript', parsed);

  // ---- 4. JS wires the required pieces. ----
  ok('app.js defines the four board columns',
     /COLUMNS\s*=\s*\[\s*['"]triage['"]\s*,\s*['"]in_progress['"]\s*,\s*['"]review['"]\s*,\s*['"]done['"]\s*\]/.test(jsSrc));
  ok('app.js polls every 5s',                 /POLL_MS\s*=\s*5000/.test(jsSrc));
  ok('app.js implements drag-and-drop',        /onDragStart|dragstart/.test(jsSrc) && /onDrop|'drop'/.test(jsSrc));
  ok('app.js uses localStorage for filters',   /localStorage\.setItem\(STORAGE_KEY/.test(jsSrc)
                                                && /localStorage\.getItem\(STORAGE_KEY/.test(jsSrc));
  ok('app.js calls /api/board/move on drop',   /fetchJson\(['"]\/api\/board\/move['"]/.test(jsSrc));
  ok('app.js calls /api/docs for content',     /\/api\/docs\//.test(jsSrc));
  ok('app.js lazy-loads doc content',         /state\.docContent\.has\(id\)/.test(jsSrc));
  ok('app.js pauses polling on visibility',    /document\.hidden/.test(jsSrc));
  ok('app.js renders an empty-state message',  /no docs yet|filtered from/.test(jsSrc));

  // ---- 5. Bundle budget. ----
  const publicDir = path.join(__dirname, '..', 'public');
  const bundleBytes = ['index.html', 'app.js', 'styles.css']
    .reduce((sum, f) => sum + fs.statSync(path.join(publicDir, f)).size, 0);
  ok(`public bundle ${bundleBytes} bytes < 200000 budget`, bundleBytes < 200000);

  // ---- 6. /api/board + /api/docs return the seeded data. ----
  const board = await requestRaw(server.port, 'GET', '/api/board');
  const boardBody = JSON.parse(board.body.toString('utf8'));
  ok('GET /api/board returns 4 seeded cards', boardBody.cards.length === 4);
  ok('cards span columns', new Set(boardBody.cards.map((c) => c.column)).size >= 3);

  const docs = await requestRaw(server.port, 'GET', '/api/docs');
  const docsBody = JSON.parse(docs.body.toString('utf8'));
  ok('GET /api/docs returns 2 seeded docs', docsBody.docs.length === 2);

  const docDetail = await requestRaw(server.port, 'GET', '/api/docs/doc_ui_1');
  const docDetailBody = JSON.parse(docDetail.body.toString('utf8'));
  ok('GET /api/docs/:id returns full content',
     docDetailBody && typeof docDetailBody.content === 'string'
     && docDetailBody.content.includes('# Alpha'));

  // ---- Cleanup. ----
  await server.close();
  try { await fsp.rm(tmpRoot, { recursive: true, force: true }); } catch (_) {}

  console.log(`\n${failed === 0 ? 'UI OK' : 'UI FAILED'} (${passed} passed, ${failed} failed)`);
  process.exit(failed === 0 ? 0 : 1);
})().catch((err) => {
  console.error('UI ERROR:', err);
  process.exit(1);
});