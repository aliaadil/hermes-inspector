-- Hermes Inspector persistence schema
-- Backed by better-sqlite3 (single file at data/inspector.db).
-- All identifiers are TEXT (TEXT affinity) for portability; JSON columns
-- store TEXT containing JSON-encoded arrays/objects.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;

-- docs: every doc Hermes emits (PR summaries, briefs, ADRs, etc.)
CREATE TABLE IF NOT EXISTS docs (
  id           TEXT PRIMARY KEY,        -- e.g. "doc_<uuid>" or content-hash
  task_id      TEXT NOT NULL,           -- kanban task id that produced the doc
  title        TEXT NOT NULL,
  content      TEXT NOT NULL,           -- markdown / raw text body
  source       TEXT NOT NULL,           -- 'pr-summary' | 'brief' | 'adr' | 'note' | ...
  created_at   TEXT NOT NULL,           -- ISO-8601 UTC
  completed_at TEXT                     -- ISO-8601 UTC; NULL while draft
);

CREATE INDEX IF NOT EXISTS idx_docs_task_id    ON docs(task_id);
CREATE INDEX IF NOT EXISTS idx_docs_created_at ON docs(created_at);
CREATE INDEX IF NOT EXISTS idx_docs_source     ON docs(source);

-- kanban: snapshot of card state for the inspector view.
-- This is a denormalized mirror of the source-of-truth kanban DB so the
-- inspector can run even when the live board is unavailable.
CREATE TABLE IF NOT EXISTS kanban (
  card_id      TEXT PRIMARY KEY,        -- kanban card id, e.g. "t_3ee2062a"
  title        TEXT NOT NULL,
  body         TEXT NOT NULL DEFAULT '',
  column       TEXT NOT NULL,           -- 'todo' | 'ready' | 'running' | 'blocked' | 'review' | 'done'
  parents_json TEXT NOT NULL DEFAULT '[]', -- JSON array of parent card ids
  assignee     TEXT,                    -- profile slug
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_kanban_column      ON kanban(column);
CREATE INDEX IF NOT EXISTS idx_kanban_assignee    ON kanban(assignee);
CREATE INDEX IF NOT EXISTS idx_kanban_updated_at  ON kanban(updated_at);