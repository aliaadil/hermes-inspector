"""JSON-file storage backend for Hermes Inspector.

Used when SQLite isn't available (locked-down systems without the
native binding). The backend is functionally identical to
:class:`SqliteStore` — the plugin code never branches on backend type.

The on-disk format is a single JSON object:

    {
      "version": 1,
      "docs":   [{"id": ..., "task_id": ..., ...}, ...],
      "kanban": [{"card_id": ..., "title": ..., ...}, ...]
    }

Writes are atomic (write to ``.tmp`` then rename) so a crash mid-write
cannot leave a half-written file.
"""
from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_VERSION = 1


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.", time.gmtime()) + \
        f"{int((time.time() % 1) * 1000):03d}Z"


def _generate_doc_id() -> str:
    return f"doc_{int(time.time() * 1000):x}_{secrets.token_hex(4)}"


class JsonStore:
    """Single-file JSON store. Thread-safe via per-instance RLock."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {"version": _VERSION, "docs": [], "kanban": []}
        self._loaded = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def init(self) -> None:
        with self._lock:
            if self._loaded:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                try:
                    raw = self.path.read_text(encoding="utf-8")
                    parsed = json.loads(raw) if raw.strip() else None
                    if isinstance(parsed, dict):
                        self._data = {
                            "version": parsed.get("version", _VERSION),
                            "docs": list(parsed.get("docs") or []),
                            "kanban": list(parsed.get("kanban") or []),
                        }
                    else:
                        self._data = {"version": _VERSION, "docs": [], "kanban": []}
                except (OSError, json.JSONDecodeError):
                    # Corrupt file — start fresh but back up the original so
                    # the operator can inspect it.
                    backup = self.path.with_suffix(self.path.suffix + ".corrupt")
                    try:
                        self.path.replace(backup)
                    except OSError:
                        pass
                    self._data = {"version": _VERSION, "docs": [], "kanban": []}
            else:
                self._flush_unlocked()
            self._loaded = True

    def close(self) -> None:
        with self._lock:
            if self._loaded:
                self._flush_unlocked()
            self._loaded = False

    # ------------------------------------------------------------------
    # docs
    # ------------------------------------------------------------------

    def save_doc(self, data: Dict[str, Any]) -> Dict[str, str]:
        if not isinstance(data, dict):
            raise TypeError("save_doc expects a dict")
        with self._lock:
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
            existing_idx = self._index_of(self._data["docs"], "id", row["id"])
            if existing_idx is None:
                self._data["docs"].append(row)
            else:
                self._data["docs"][existing_idx] = row
            self._flush_unlocked()
            return {"id": row["id"]}

    def get_doc(self, doc_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for row in self._data["docs"]:
                if row.get("id") == str(doc_id):
                    return dict(row)
            return None

    def list_docs(self, task_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            docs = self._data["docs"]
            if task_id:
                rows = [dict(r) for r in docs if r.get("task_id") == str(task_id)]
            else:
                rows = [dict(r) for r in docs]
            rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
            return rows[: int(limit)] if not task_id else rows

    # ------------------------------------------------------------------
    # kanban
    # ------------------------------------------------------------------

    def upsert_card(self, data: Dict[str, Any]) -> Dict[str, str]:
        if not isinstance(data, dict):
            raise TypeError("upsert_card expects a dict")
        with self._lock:
            card_id = str(data.get("card_id") or data.get("id") or "")
            if not card_id:
                raise ValueError("upsert_card requires 'card_id'")
            ts = _now_iso()
            row = {
                "card_id": card_id,
                "title": str(data.get("title", "")),
                "body": str(data.get("body", "")),
                "column": str(data.get("column", "ready")),
                "parents": list(data.get("parents") or []),
                "assignee": data.get("assignee"),
                "created_at": data.get("created_at") or ts,
                "updated_at": data.get("updated_at") or ts,
            }
            idx = self._index_of(self._data["kanban"], "card_id", card_id)
            if idx is None:
                self._data["kanban"].append(row)
            else:
                # Preserve original created_at on update.
                prev = self._data["kanban"][idx]
                row["created_at"] = prev.get("created_at") or row["created_at"]
                self._data["kanban"][idx] = row
            self._flush_unlocked()
            return {"card_id": card_id}

    def get_card(self, card_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for row in self._data["kanban"]:
                if row.get("card_id") == str(card_id):
                    return dict(row)
            return None

    def move_card(self, card_id: str, column: str) -> Optional[Dict[str, str]]:
        with self._lock:
            for row in self._data["kanban"]:
                if row.get("card_id") == str(card_id):
                    row["column"] = str(column)
                    row["updated_at"] = _now_iso()
                    self._flush_unlocked()
                    return {"card_id": str(card_id), "column": str(column)}
            return None

    def list_board(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = [dict(r) for r in self._data["kanban"]]
            rows.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
            return rows

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _index_of(rows: List[Dict[str, Any]], key: str, value: str) -> Optional[int]:
        for i, row in enumerate(rows):
            if row.get(key) == value:
                return i
        return None

    def _flush_unlocked(self) -> None:
        """Write the current state to disk atomically. Caller holds the lock."""
        payload = json.dumps(self._data, indent=2, sort_keys=True)
        # Atomic replace: write to a temp file in the same directory, then rename.
        fd, tmp_path = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    # fsync not supported on every filesystem; not fatal.
                    pass
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def reset(self) -> None:
        with self._lock:
            self._data = {"version": _VERSION, "docs": [], "kanban": []}
            self._flush_unlocked()


__all__ = ["JsonStore"]