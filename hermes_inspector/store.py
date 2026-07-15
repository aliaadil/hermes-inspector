"""Store factory + plugin-level singleton.

The plugin picks a backend once at ``register(ctx)`` time and reuses
one store instance for the life of the process. Tests can call
``set_store(...)`` to inject a fake.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional, Protocol


class Store(Protocol):
    """Common interface used by hooks + the FastAPI router."""

    def init(self) -> None: ...
    def close(self) -> None: ...
    def save_doc(self, data: dict) -> dict: ...
    def get_doc(self, doc_id: str) -> Optional[dict]: ...
    def list_docs(self, task_id: Optional[str] = None, limit: int = 100) -> list: ...
    def upsert_card(self, data: dict) -> dict: ...
    def get_card(self, card_id: str) -> Optional[dict]: ...
    def move_card(self, card_id: str, column: str) -> Optional[dict]: ...
    def list_board(self) -> list: ...
    def reset(self) -> None: ...


_store: Optional[Store] = None
_lock = threading.Lock()


def make_store(path: Path | str, backend: str = "sqlite") -> Store:
    """Construct a store for the given backend. Validates the backend name."""
    backend = (backend or "sqlite").lower().strip()
    if backend == "sqlite":
        from hermes_inspector.store_sqlite import SqliteStore

        return SqliteStore(path)
    if backend == "json":
        from hermes_inspector.store_json import JsonStore

        return JsonStore(path)
    raise ValueError(
        f"Unknown inspector backend {backend!r}; expected 'sqlite' or 'json'"
    )


def get_store() -> Store:
    """Return the process-wide store. Raises if ``set_store`` has not been called."""
    if _store is None:
        raise RuntimeError(
            "Hermes inspector store has not been initialized; "
            "the plugin's register(ctx) must run before any hook fires."
        )
    return _store


def set_store(store: Optional[Store]) -> None:
    """Install (or clear with None) the process-wide store. Plugin uses this once at register time."""
    global _store
    with _lock:
        _store = store


__all__ = ["Store", "make_store", "get_store", "set_store"]