// Hermes Inspector storage module — SQLite (better-sqlite3) backend.
//
// All public functions return Promises so callers get a uniform async API
// regardless of backend (the JSON-file fallback uses fs.promises, this
// backend wraps the synchronous better-sqlite3 API in Promise.resolve()).
//
// Usage:
//   const store = require('./store');
//   await store.init({ path: './data/inspector.db' });
//   await store.saveDoc({ id, task_id, title, content, source });
//
// All timestamps are ISO-8601 UTC strings. parents_json is JSON-encoded.

'use strict';

const fs = require('fs');
const path = require('path');
const Database = require('better-sqlite3');

const DEFAULT_PATH = path.join(__dirname, '..', 'data', 'inspector.db');

let db = null;
let stmts = null;

function ensureDir(filePath) {
  const dir = path.dirname(filePath);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
}

function prepare(db) {
  return {
    insertDoc: db.prepare(`
      INSERT INTO docs (id, task_id, title, content, source, created_at, completed_at)
      VALUES (@id, @task_id, @title, @content, @source, @created_at, @completed_at)
      ON CONFLICT(id) DO UPDATE SET
        title        = excluded.title,
        content      = excluded.content,
        source       = excluded.source,
        completed_at = excluded.completed_at
    `),
    getDoc: db.prepare(`SELECT * FROM docs WHERE id = ?`),
    listDocs: db.prepare(`
      SELECT id, task_id, title, source, created_at, completed_at
      FROM docs
      ORDER BY created_at DESC
      LIMIT @limit
    `),
    listDocsByTask: db.prepare(`
      SELECT id, task_id, title, source, created_at, completed_at
      FROM docs
      WHERE task_id = ?
      ORDER BY created_at DESC
    `),
    upsertCard: db.prepare(`
      INSERT INTO kanban (card_id, title, body, column, parents_json, assignee, created_at, updated_at)
      VALUES (@card_id, @title, @body, @column, @parents_json, @assignee, @created_at, @updated_at)
      ON CONFLICT(card_id) DO UPDATE SET
        title         = excluded.title,
        body          = excluded.body,
        column        = excluded.column,
        parents_json  = excluded.parents_json,
        assignee      = excluded.assignee,
        updated_at    = excluded.updated_at
    `),
    getCard: db.prepare(`SELECT * FROM kanban WHERE card_id = ?`),
    listBoard: db.prepare(`SELECT * FROM kanban ORDER BY updated_at DESC`),
    moveCard: db.prepare(`
      UPDATE kanban
         SET column = @column, updated_at = @updated_at
       WHERE card_id = @card_id
    `),
  };
}

function nowIso() {
  return new Date().toISOString();
}

// init({ path?: string }) -> Promise<void>
// Opens (or creates) the SQLite file, runs the schema, and prepares statements.
// Idempotent: calling twice is a no-op if already initialized to the same file.
async function init(opts = {}) {
  if (db) return;
  const file = opts.path || DEFAULT_PATH;
  ensureDir(file);
  db = new Database(file);
  const schema = fs.readFileSync(path.join(__dirname, 'schema.sql'), 'utf8');
  db.exec(schema);
  stmts = prepare(db);
}

async function close() {
  if (!db) return;
  db.close();
  db = null;
  stmts = null;
}

// saveDoc({ id, task_id, title, content, source, completed_at? }) -> Promise<{id}>
// `id` is auto-generated as `doc_<random>` if omitted.
async function saveDoc(input) {
  if (!db) throw new Error('store: init() not called');
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
  stmts.insertDoc.run(row);
  return { id };
}

async function getDoc(id) {
  if (!db) throw new Error('store: init() not called');
  return stmts.getDoc.get(id) || null;
}

// listDocs({ task_id?, limit? }) -> Promise<DocRow[]>
async function listDocs(opts = {}) {
  if (!db) throw new Error('store: init() not called');
  if (opts.task_id) return stmts.listDocsByTask.all(opts.task_id);
  return stmts.listDocs.all({ limit: opts.limit ?? 100 });
}

// upsertCard({ card_id, title, body?, column, parents?, assignee?, created_at?, updated_at? })
// -> Promise<{card_id}>
async function upsertCard(input) {
  if (!db) throw new Error('store: init() not called');
  const ts = nowIso();
  const row = {
    card_id: String(input.card_id),
    title: String(input.title),
    body: String(input.body ?? ''),
    column: String(input.column),
    parents_json: JSON.stringify(input.parents ?? []),
    assignee: input.assignee ?? null,
    created_at: input.created_at || ts,
    updated_at: input.updated_at || ts,
  };
  stmts.upsertCard.run(row);
  return { card_id: row.card_id };
}

async function getCard(cardId) {
  if (!db) throw new Error('store: init() not called');
  const row = stmts.getCard.get(cardId);
  if (!row) return null;
  return { ...row, parents: JSON.parse(row.parents_json) };
}

async function listBoard() {
  if (!db) throw new Error('store: init() not called');
  return stmts.listBoard.all().map((r) => ({ ...r, parents: JSON.parse(r.parents_json) }));
}

// moveCard(card_id, column) -> Promise<{card_id, column}>
// No-op (returns null) if the card doesn't exist.
async function moveCard(cardId, column) {
  if (!db) throw new Error('store: init() not called');
  const info = stmts.moveCard.run({ card_id: String(cardId), column: String(column), updated_at: nowIso() });
  if (info.changes === 0) return null;
  return { card_id: cardId, column };
}

// Test helper: wipe all rows (does NOT drop schema). Used by tests + smoke.
async function _reset() {
  if (!db) throw new Error('store: init() not called');
  db.exec('DELETE FROM docs; DELETE FROM kanban;');
}

module.exports = {
  init,
  close,
  saveDoc,
  getDoc,
  listDocs,
  upsertCard,
  getCard,
  listBoard,
  moveCard,
  _reset,
};