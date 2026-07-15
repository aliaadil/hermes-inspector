"""SQLite storage backend for Hermes Inspector.

Mirrors the JS store at ``plugins/hermes-inspector/src/store.js`` so the
two formats are interchangeable (same column names, same JSON-encoded
``parents_json`` column). Schema is kept in sync with
``plugins/hermes-inspector/src/schema.sql``.

Public API is intentionally small and synchronous so a plugin hook
callback can use it without awaiting a coroutine.
"""
from __future__ import annotations

import json
import sqlite3
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Schema lives next to the JS implementation for parity. We embed it
# here as a constant so the Python store can run on systems where
# ``Path(__file__).parent / "schema.sql"`` would not be writable.
SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS docs (
  id           TEXT PRIMARY KEY,
  task_id      TEXT NOT NULL,
  title        TEXT NOT NULL,
  content      TEXT NOT NULL,
  source       TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_docs_task_id    ON docs(task_id);
CREATE INDEX IF NOT EXISTS idx_docs_created_at ON docs(created_at);
CREATE INDEX IF NOT EXISTS idx_docs_source     ON docs(source);

CREATE TABLE IF NOT EXISTS kanban (
  card_id      TEXT PRIMARY KEY,
  title        TEXT NOT NULL,
  body         TEXT NOT NULL DEFAULT '',
  column       TEXT NOT NULL,
  parents_json TEXT NOT NULL DEFAULT '[]',
  assignee     TEXT,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_kanban_column      ON kanban(column);
CREATE INDEX IF NOT EXISTS idx_kanban_assignee    ON kanban(assignee);
CREATE INDEX IF NOT EXISTS idx_kanban_updated_at  ON kanban(updated_at);
"""


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (millisecond precision)."""
    return time.strftime("%Y-%m-%dT%H:%M:%S.", time.gmtime()) + \
        f"{int((time.time() % 1) * 1000):03d}Z"


def _generate_doc_id() -> str:
    """Generate a doc id in the same shape the JS store uses."""
    return f"doc_{int(time.time() * 1000):x}_{secrets.token_hex(4)}"


class SqliteStore:
    """Synchronous SQLite-backed store. Thread-safe via a per-instance lock."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def init(self) -> None:
        """Open the database, create parent directories, run the schema.

        Idempotent — a second call against an already-open store is a no-op.
        """
        with self._lock:
            if self._conn is not None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # ``check_same_thread=False`` lets FastAPI's TestClient (which
            # routes handlers onto a worker thread) reuse the connection
            # we open here. All access is still serialized through ``_lock``.
            conn = sqlite3.connect(str(self.path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.executescript(SCHEMA_SQL)
            conn.commit()
            self._conn = conn

    def close(self) -> None:
        with self._lock:
            if self._conn is None:
                return
            self._conn.close()
            self._conn = None

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("SqliteStore.init() must be called before use")
        return self._conn

    # ------------------------------------------------------------------
    # docs
    # ------------------------------------------------------------------

    def save_doc(self, data: Dict[str, Any]) -> Dict[str, str]:
        """Insert or update a doc row. Returns ``{"id": "..."}``."""
        if not isinstance(data, dict):
            raise TypeError("save_doc expects a dict")
        with self._lock:
            conn = self._require_conn()
            doc_id = data.get("id") or _generate_doc_id()
            row = {
                "id": str(doc_id),
                "task_id": str(data.get("task_id", "")),
                "title": str(data.get("title", "")),
                "content": str(data.get("content", "")),
                "source": str(data.get("source", "")),
                "created_at": data.get("created_at") or _now_iso(),
                "completed_at": data.get("completed_at"),
            }
            conn.execute(
                """
                INSERT INTO docs (id, task_id, title, content, source, created_at, completed_at)
                VALUES (:id, :task_id, :title, :content, :source, :created_at, :completed_at)
                ON CONFLICT(id) DO UPDATE SET
                    title        = excluded.title,
                    content      = excluded.content,
                    source       = excluded.source,
                    task_id      = excluded.task_id,
                    completed_at = excluded.completed_at
                """,
                row,
            )
            conn.commit()
            return {"id": row["id"]}

    def get_doc(self, doc_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            conn = self._require_conn()
            row = conn.execute("SELECT * FROM docs WHERE id = ?", (str(doc_id),)).fetchone()
            return dict(row) if row else None

    def list_docs(self, task_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._require_conn()
            if task_id:
                rows = conn.execute(
                    """
                    SELECT id, task_id, title, source, created_at, completed_at
                      FROM docs
                     WHERE task_id = ?
                     ORDER BY created_at DESC
                    """,
                    (str(task_id),),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, task_id, title, source, created_at, completed_at
                      FROM docs
                     ORDER BY created_at DESC
                     LIMIT ?
                    """,
                    (int(limit),),
                ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # kanban
    # ------------------------------------------------------------------

    def upsert_card(self, data: Dict[str, Any]) -> Dict[str, str]:
        """Insert or update a kanban card snapshot. Returns ``{"card_id": "..."}``."""
        if not isinstance(data, dict):
            raise TypeError("upsert_card expects a dict")
        with self._lock:
            conn = self._require_conn()
            ts = _now_iso()
            card_id = str(data.get("card_id") or data.get("id") or "")
            if not card_id:
                raise ValueError("upsert_card requires 'card_id'")
            row = {
                "card_id": card_id,
                "title": str(data.get("title", "")),
                "body": str(data.get("body", "")),
                "column": str(data.get("column", "ready")),
                "parents_json": json.dumps(data.get("parents") or []),
                "assignee": data.get("assignee"),
                "created_at": data.get("created_at") or ts,
                "updated_at": data.get("updated_at") or ts,
            }
            conn.execute(
                """
                INSERT INTO kanban (card_id, title, body, column, parents_json, assignee, created_at, updated_at)
                VALUES (:card_id, :title, :body, :column, :parents_json, :assignee, :created_at, :updated_at)
                ON CONFLICT(card_id) DO UPDATE SET
                    title         = excluded.title,
                    body          = excluded.body,
                    column        = excluded.column,
                    parents_json  = excluded.parents_json,
                    assignee      = excluded.assignee,
                    updated_at    = excluded.updated_at
                """,
                row,
            )
            conn.commit()
            return {"card_id": row["card_id"]}

    def get_card(self, card_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            conn = self._require_conn()
            row = conn.execute(
                "SELECT * FROM kanban WHERE card_id = ?", (str(card_id),)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                d["parents"] = json.loads(d.get("parents_json") or "[]")
            except json.JSONDecodeError:
                d["parents"] = []
            return d

    def move_card(self, card_id: str, column: str) -> Optional[Dict[str, str]]:
        with self._lock:
            conn = self._require_conn()
            cur = conn.execute(
                """
                UPDATE kanban
                   SET column = ?, updated_at = ?
                 WHERE card_id = ?
                """,
                (str(column), _now_iso(), str(card_id)),
            )
            conn.commit()
            if cur.rowcount == 0:
                return None
            return {"card_id": str(card_id), "column": str(column)}

    def list_board(self) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._require_conn()
            rows = conn.execute(
                "SELECT * FROM kanban ORDER BY updated_at DESC"
            ).fetchall()
            out: List[Dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                try:
                    d["parents"] = json.loads(d.get("parents_json") or "[]")
                except json.JSONDecodeError:
                    d["parents"] = []
                out.append(d)
            return out

    # ------------------------------------------------------------------
    # test helper
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Wipe all rows. Used by tests + smoke harnesses."""
        with self._lock:
            conn = self._require_conn()
            conn.executescript("DELETE FROM docs; DELETE FROM kanban;")
            conn.commit()


__all__ = ["SqliteStore", "SCHEMA_SQL"]