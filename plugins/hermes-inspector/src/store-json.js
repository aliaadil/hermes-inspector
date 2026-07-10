// Hermes Inspector storage — JSON-file fallback.
// Same public API as store.js but backed by two .json files. Use this when
// better-sqlite3's native binding can't be built (sandbox, no compiler, etc.).
// Not for production at scale: no indexing, no concurrency guarantees.

'use strict';

const fs = require('fs');
const fsp = require('fs/promises');
const path = require('path');

const DEFAULT_DIR = path.join(__dirname, '..', 'data', 'json-fallback');

let docs = [];
let cards = [];
let dataDir = null;

function nowIso() {
  return new Date().toISOString();
}

async function loadFromDisk(dir) {
  const docsPath = path.join(dir, 'docs.json');
  const kanbanPath = path.join(dir, 'kanban.json');
  docs = fs.existsSync(docsPath) ? JSON.parse(await fsp.readFile(docsPath, 'utf8')) : [];
  cards = fs.existsSync(kanbanPath) ? JSON.parse(await fsp.readFile(kanbanPath, 'utf8')) : [];
}

async function flushDocs() {
  await fsp.writeFile(path.join(dataDir, 'docs.json'), JSON.stringify(docs, null, 2));
}
async function flushCards() {
  await fsp.writeFile(path.join(dataDir, 'kanban.json'), JSON.stringify(cards, null, 2));
}

async function init(opts = {}) {
  dataDir = opts.path || DEFAULT_DIR;
  await fsp.mkdir(dataDir, { recursive: true });
  await loadFromDisk(dataDir);
}

async function close() {
  docs = [];
  cards = [];
  dataDir = null;
}

async function saveDoc(input) {
  if (!dataDir) throw new Error('store-json: init() not called');
  const id = input.id || `doc_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
  const row = {
    id,
    task_id: String(input.task_id),
    title: String(input.title),
    content: String(input.content ?? ''),
    source: String(input.source),
    created_at: input.created_at || nowIso(),
    completed_at: input.completed_at ?? null,
  };
  const idx = docs.findIndex((d) => d.id === id);
  if (idx >= 0) docs[idx] = row; else docs.push(row);
  await flushDocs();
  return { id };
}

async function getDoc(id) {
  if (!dataDir) throw new Error('store-json: init() not called');
  return docs.find((d) => d.id === id) || null;
}

async function listDocs(opts = {}) {
  if (!dataDir) throw new Error('store-json: init() not called');
  const filtered = opts.task_id ? docs.filter((d) => d.task_id === opts.task_id) : docs;
  const rows = filtered
    .map(({ id, task_id, title, source, created_at, completed_at }) => ({
      id, task_id, title, source, created_at, completed_at,
    }))
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
  return opts.limit ? rows.slice(0, opts.limit) : rows;
}

async function upsertCard(input) {
  if (!dataDir) throw new Error('store-json: init() not called');
  const ts = nowIso();
  const row = {
    card_id: String(input.card_id),
    title: String(input.title),
    body: String(input.body ?? ''),
    column: String(input.column),
    parents: input.parents ?? [],
    assignee: input.assignee ?? null,
    created_at: input.created_at || ts,
    updated_at: input.updated_at || ts,
  };
  const idx = cards.findIndex((c) => c.card_id === row.card_id);
  if (idx >= 0) {
    cards[idx] = { ...cards[idx], ...row, updated_at: ts };
  } else {
    cards.push(row);
  }
  await flushCards();
  return { card_id: row.card_id };
}

async function getCard(cardId) {
  if (!dataDir) throw new Error('store-json: init() not called');
  return cards.find((c) => c.card_id === cardId) || null;
}

async function listBoard() {
  if (!dataDir) throw new Error('store-json: init() not called');
  return [...cards].sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1));
}

async function moveCard(cardId, column) {
  if (!dataDir) throw new Error('store-json: init() not called');
  const c = cards.find((x) => x.card_id === cardId);
  if (!c) return null;
  c.column = String(column);
  c.updated_at = nowIso();
  await flushCards();
  return { card_id: cardId, column };
}

async function _reset() {
  if (!dataDir) throw new Error('store-json: init() not called');
  docs = [];
  cards = [];
  await flushDocs();
  await flushCards();
}

module.exports = { init, close, saveDoc, getDoc, listDocs, upsertCard, getCard, listBoard, moveCard, _reset };