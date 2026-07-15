#!/usr/bin/env node
// Integration test: exercise the full plugin lifecycle end-to-end.
// Boots the plugin in standalone mode, fires a fake task_created event,
// reads back via the HTTP API, then kills the server, restarts, and checks
// the data still survives.
//
// Usage: node test/integration.js [backend]   (sqlite | json)
//
// Prints PASS/FAIL per assertion and exits non-zero on any failure.

'use strict';

const fs = require('fs');
const fsp = require('fs/promises');
const path = require('path');
const http = require('http');

const backend = process.argv[2] || 'sqlite';
const tmpRoot = path.join(__dirname, '..', 'data', `integration-${backend}-${Date.now()}`);
fs.mkdirSync(tmpRoot, { recursive: true });
const dataPath = backend === 'json'
  ? path.join(tmpRoot, 'json-fallback')
  : path.join(tmpRoot, 'inspector.db');

let passed = 0, failed = 0;
function ok(label, cond) {
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${label}`);
  if (cond) passed++; else failed++;
}

const plugin = require('../index');

function requestJson(port, method, urlPath, body) {
  return new Promise((resolve, reject) => {
    const data = body ? Buffer.from(JSON.stringify(body), 'utf8') : null;
    const req = http.request({
      host: '127.0.0.1', port, method, path: urlPath,
      headers: data ? { 'content-type': 'application/json', 'content-length': data.length } : {},
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const raw = Buffer.concat(chunks).toString('utf8');
        let parsed; try { parsed = JSON.parse(raw); } catch { parsed = raw; }
        resolve({ status: res.statusCode, body: parsed });
      });
    });
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

async function bootPlugin() {
  const handle = await plugin.runStandalone({ port: 0, data_path: dataPath, backend });
  return handle;
}

(async () => {
  // ---- Boot 1: emit events, then verify they're queryable via HTTP. ----
  let server = await bootPlugin();
  ok('plugin boots (backend=' + backend + ')', server.port > 0);

  // Fire a task_created — should create a card in 'ready'.
  await plugin.onEvent('task_created', {
    id: 't_integration_001',
    title: 'Integration test card',
    body: 'created by integration test',
    parents: [],
    assignee: 'builder',
  });
  // Fire a doc_emitted — should create a doc row.
  await plugin.onEvent('doc_emitted', {
    id: 'doc_integration_001',
    task_id: 't_integration_001',
    title: 'Hello doc',
    content: '# Hello',
    source: 'note',
  });
  // Promote via task_completed.
  await plugin.onEvent('task_completed', { id: 't_integration_001', title: 'Integration test card' });

  // /api/board should show the card in 'done'.
  const board1 = await requestJson(server.port, 'GET', '/api/board');
  ok('GET /api/board returns 200', board1.status === 200);
  ok('board contains our card', board1.body.cards.some((c) => c.card_id === 't_integration_001'));
  const card = board1.body.cards.find((c) => c.card_id === 't_integration_001');
  ok('card column is done after task_completed', card && card.column === 'done');

  // /api/docs should list our doc.
  const docs1 = await requestJson(server.port, 'GET', '/api/docs?task_id=t_integration_001');
  ok('GET /api/docs?task_id returns 200', docs1.status === 200);
  ok('doc list contains our doc', docs1.body.docs.some((d) => d.id === 'doc_integration_001'));

  // /api/docs/:id
  const docDetail = await requestJson(server.port, 'GET', '/api/docs/doc_integration_001');
  ok('GET /api/docs/:id returns 200', docDetail.status === 200);
  ok('doc detail has correct content', docDetail.body.content === '# Hello');

  // /api/board/move happy path
  const moved = await requestJson(server.port, 'POST', '/api/board/move',
    { card_id: 't_integration_001', to_column: 'review' });
  ok('POST /api/board/move returns 200', moved.status === 200);
  ok('move response has column=review', moved.body.column === 'review');

  // /api/board/move with bad input
  const bad = await requestJson(server.port, 'POST', '/api/board/move', { card_id: 't_integration_001' });
  ok('POST /api/board/move rejects missing to_column (400)', bad.status === 400);

  const badCol = await requestJson(server.port, 'POST', '/api/board/move',
    { card_id: 't_integration_001', to_column: 'gibberish' });
  ok('POST /api/board/move rejects invalid column (400)', badCol.status === 400);

  // Static asset
  const html = await requestJson(server.port, 'GET', '/');
  ok('GET / returns 200', html.status === 200);
  ok('GET / serves HTML', typeof html.body === 'string' && html.body.includes('Hermes Inspector'));

  // ---- Kill the server, restart against the same data file. ----
  await server.close();

  server = await bootPlugin();
  ok('plugin restarts against same data file', server.port > 0);

  const board2 = await requestJson(server.port, 'GET', '/api/board');
  ok('after restart, card still present', board2.body.cards.some((c) => c.card_id === 't_integration_001'));
  const card2 = board2.body.cards.find((c) => c.card_id === 't_integration_001');
  ok('after restart, column preserved as review', card2 && card2.column === 'review');

  const docs2 = await requestJson(server.port, 'GET', '/api/docs?task_id=t_integration_001');
  ok('after restart, doc still present', docs2.body.docs.some((d) => d.id === 'doc_integration_001'));

  // ---- Cleanup. ----
  await server.close();
  try { await fsp.rm(tmpRoot, { recursive: true, force: true }); } catch (_) {}

  console.log(`\n${failed === 0 ? 'INTEGRATION OK' : 'INTEGRATION FAILED'} (${passed} passed, ${failed} failed)`);
  process.exit(failed === 0 ? 0 : 1);
})().catch((err) => {
  console.error('INTEGRATION ERROR:', err);
  process.exit(1);
});