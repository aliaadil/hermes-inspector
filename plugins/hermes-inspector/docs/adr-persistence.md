# ADR: Hermes Inspector persistence backend

**Status:** accepted
**Date:** 2026-07-10
**Decision driver:** `t_3ee2062a` — design data persistence schema for docs and kanban state.

## Context

The Hermes Inspector plugin needs to outlive any single kanban task: every
doc Hermes emits (PR summaries, briefs, ADRs, notes) and a snapshot of the
kanban board state must persist across task completion, plugin restarts, and
machine reboots. The plugin runs inside the Hermes Node.js process and must
work on a self-hosted Linux box without external services.

Requirements:

- Survive process restarts and crash loops.
- Survive without a running database server (no daemon to babysit).
- Cheap to back up (single file or single directory).
- Fast enough for the inspector's read-heavy workload (list docs, list board).
- Optional: a JSON-file fallback so the plugin still installs on systems
  where `better-sqlite3`'s native binding can't be compiled.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **SQLite via `better-sqlite3`** | Single file, zero ops, synchronous API, fast, WAL mode, ubiquitous. | Native binding → must compile on install. |
| **SQLite via `sqlite3` (async)** | Pure JS fallback. | Async API everywhere is awkward in a sync-heavy Node codebase. |
| **LowDB / nedb** | Pure JS, single JSON file. | No real indexing, full-file rewrites, concurrency caveats. |
| **Postgres / MySQL** | Production-grade. | Needs a daemon. Overkill for a single-plugin sidecar. |
| **BoltDB / LevelDB** | Embedded, fast key-value. | Not relational; can't index by `task_id` cleanly. |
| **JSON-file fallback** | Zero deps, trivial backup. | No indexing, no atomicity, slow past ~1k rows. |

## Decision

**Primary: SQLite via `better-sqlite3`.** Synchronous API, single file at
`data/inspector.db`, WAL journaling. Backups are `cp inspector.db inspector.db.bak`.

**Fallback: JSON file backend (`src/store-json.js`)** exposing the same
public API. Selected automatically when `better-sqlite3` cannot load (e.g.
no prebuilt binary, no compiler). Not intended for production scale —
it's the path that lets the plugin keep working on a stripped-down box.

## Schema

Two tables, both TEXT-affinity for portability:

```sql
docs(
  id TEXT PK,
  task_id TEXT,
  title TEXT,
  content TEXT,
  source TEXT,
  created_at TEXT,      -- ISO-8601 UTC
  completed_at TEXT     -- nullable
)

kanban(
  card_id TEXT PK,
  title TEXT,
  body TEXT,
  column TEXT,          -- 'todo'|'ready'|'running'|'blocked'|'review'|'done'
  parents_json TEXT,    -- JSON array of card_ids
  assignee TEXT,
  created_at TEXT,
  updated_at TEXT
)
```

Indexes: `docs(task_id)`, `docs(created_at)`, `docs(source)`,
`kanban(column)`, `kanban(assignee)`, `kanban(updated_at)`.

## Consequences

- **Pros:** zero ops, trivial backups, fast reads, predictable performance.
- **Pros:** same API for both backends → tests can run against either.
- **Cons:** must commit `data/inspector.db*` to `.gitignore`.
- **Cons:** `parents_json` is a stringly-typed JSON column — keep parsing
  centralized in `store.js` so callers always see an array.

## Reversal path

If the inspector's workload grows past ~10k docs / day or moves to a
multi-process deployment, port to Postgres. The public API is small
(`saveDoc`, `listDocs`, `getDoc`, `upsertCard`, `listBoard`, `moveCard`) —
swap the implementation, keep the API.