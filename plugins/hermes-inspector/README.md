# Hermes Inspector

Storage layer for the Hermes Inspector plugin. Persists every doc Hermes
emits (PR summaries, briefs, ADRs, notes) plus a snapshot of kanban board
state across task completion and process restarts.

## Layout

```
plugins/hermes-inspector/
├── src/
│   ├── schema.sql       # SQLite schema (docs + kanban tables, indexes)
│   ├── store.js         # better-sqlite3 backend (primary)
│   └── store-json.js    # JSON-file fallback (same API)
├── docs/
│   └── adr-persistence.md  # ADR: why SQLite + JSON fallback
├── test/
│   └── smoke.js         # writes/reads/moves a doc + card, both backends
├── data/                # gitignored: inspector.db (+ wal/shm) lives here
└── package.json
```

## Quick start

```js
const store = require('./src/store');          // sqlite
// const store = require('./src/store-json');   // fallback

await store.init({ path: './data/inspector.db' });

await store.saveDoc({
  task_id: 't_abc',
  title: 'PR summary #42',
  content: '...',
  source: 'pr-summary',
});

await store.upsertCard({
  card_id: 't_abc',
  title: 'Ship feature X',
  column: 'ready',
  parents: ['t_parent'],
  assignee: 'builder',
});

await store.moveCard('t_abc', 'done');

const docs = await store.listDocs({ task_id: 't_abc' });
const board = await store.listBoard();
```

## Smoke test

```
node test/smoke.js sqlite
node test/smoke.js json
```

Both must print `SMOKE OK` with all `PASS` lines.

## Schema

See `src/schema.sql`. Indexes on `docs(task_id, created_at, source)` and
`kanban(column, assignee, updated_at)`. WAL mode, `foreign_keys=ON`.

## Backup

```
cp data/inspector.db data/inspector.db.bak
```

WAL means you may also need `inspector.db-wal` for an exact point-in-time
copy — either `sqlite3 inspector.db .backup inspector.db.bak` or stop
writes briefly while copying.