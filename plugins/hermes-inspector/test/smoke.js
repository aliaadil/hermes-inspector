#!/usr/bin/env node
// Smoke test: write a doc + a card, read them back, move the card, list the board.
// Usage: node test/smoke.js [backend]
//   backend = 'sqlite' (default) | 'json'

'use strict';

const path = require('path');
const fs = require('fs');

const backend = process.argv[2] || 'sqlite';
const tmpData = path.join(__dirname, `..`, 'data', `smoke-${backend}-${Date.now()}`);

const store = backend === 'json'
  ? require('../src/store-json')
  : require('../src/store');

function ok(label, cond) {
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${label}`);
  if (!cond) process.exitCode = 1;
}

(async () => {
  await store.init({ path: backend === 'json' ? tmpData : path.join(tmpData, 'inspector.db') });

  // --- DOCS ---
  const { id: docId } = await store.saveDoc({
    task_id: 't_smoke_001',
    title: 'Smoke doc title',
    content: '# Hello\n\nThis is a smoke doc.',
    source: 'note',
  });
  ok('saveDoc returned id', !!docId);

  const doc = await store.getDoc(docId);
  ok('getDoc returns saved doc', doc && doc.title === 'Smoke doc title' && doc.source === 'note');

  const docList = await store.listDocs({ task_id: 't_smoke_001' });
  ok('listDocs filters by task_id', docList.length === 1 && docList[0].id === docId);

  // --- KANBAN ---
  const { card_id } = await store.upsertCard({
    card_id: 't_smoke_001',
    title: 'Smoke card',
    body: 'A test card',
    column: 'ready',
    parents: ['t_parent_x'],
    assignee: 'builder',
  });
  ok('upsertCard returned card_id', card_id === 't_smoke_001');

  const card = await store.getCard('t_smoke_001');
  ok('getCard returns saved card',
     card && card.title === 'Smoke card' && card.column === 'ready'
     && Array.isArray(card.parents) && card.parents[0] === 't_parent_x'
     && card.assignee === 'builder');

  const moved = await store.moveCard('t_smoke_001', 'done');
  ok('moveCard moved to done', moved && moved.column === 'done');

  const cardAfter = await store.getCard('t_smoke_001');
  ok('getCard reflects move', cardAfter.column === 'done');

  const board = await store.listBoard();
  ok('listBoard returns the card', board.length === 1 && board[0].card_id === 't_smoke_001');

  // --- CLEANUP ---
  await store.close();
  if (backend === 'json') {
    fs.rmSync(tmpData, { recursive: true, force: true });
  } else {
    // SQLite: remove the db file (+wal/-shm if present)
    for (const ext of ['', '-wal', '-shm', '-journal']) {
      try { fs.unlinkSync(path.join(tmpData, `inspector.db${ext}`)); } catch (_) {}
    }
    try { fs.rmdirSync(tmpData); } catch (_) {}
  }

  console.log(process.exitCode ? '\nSMOKE FAILED' : '\nSMOKE OK');
})().catch((err) => {
  console.error('SMOKE ERROR:', err);
  process.exit(1);
});