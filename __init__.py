"""Hermes Inspector — repo-root plugin entry point.

This file is the contract surface ``hermes plugins install
aliaadil/hermes-inspector`` expects. The plugin loader will:

1. Clone the repo into ``~/.hermes/plugins/hermes-inspector/``.
2. Read ``./plugin.yaml`` for the manifest.
3. Import this file (``./__init__.py``) and call ``register(ctx)``.

``register(ctx)`` wires:

* one persistent store (sqlite primary, json fallback) into the
  process-wide store singleton
* the three real Hermes kanban hook callbacks
* the ``inspector_emit_doc`` tool agents call to capture docs

The dashboard plugin manifest under ``./dashboard/manifest.json`` is
discovered independently by ``hermes_cli.web_server`` and mounts the
FastAPI ``router`` exported from ``hermes_inspector.api``.

Configuration via env vars (read at register time):

* ``HERMES_INSPECTOR_DATA_DIR``  — directory for the store file
  (default ``./data`` relative to the install root)
* ``HERMES_INSPECTOR_BACKEND``   — ``sqlite`` (default) or ``json``
* ``HERMES_INSPECTOR_DB_NAME``   — filename (default ``inspector.db``)
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _resolve_data_dir() -> Path:
    """Where the inspector should keep its on-disk store."""
    env = os.environ.get("HERMES_INSPECTOR_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    # ``register`` is called from the installed plugin directory. The
    # Hermes loader sets cwd to that directory; using ``Path.cwd()``
    # keeps the store next to the plugin when no override is given.
    return (Path.cwd() / "data").resolve()


def _resolve_backend() -> str:
    backend = (os.environ.get("HERMES_INSPECTOR_BACKEND") or "sqlite").strip().lower()
    if backend not in {"sqlite", "json"}:
        raise ValueError(
            f"HERMES_INSPECTOR_BACKEND must be 'sqlite' or 'json', got {backend!r}"
        )
    return backend


def _resolve_db_filename(backend: str) -> str:
    name = os.environ.get("HERMES_INSPECTOR_DB_NAME")
    if name:
        return name
    return "inspector.json" if backend == "json" else "inspector.db"


def _ensure_package_on_path() -> None:
    """Add the installed plugin directory to ``sys.path`` if needed.

    Hermes' plugin loader imports this file via a synthetic module
    name (``hermes_plugins.hermes_inspector``), which leaves the
    inner ``hermes_inspector/`` package invisible to ``import`` until
    the install directory is on ``sys.path``. Tests bypass the loader
    and need this too.
    """
    here = Path(__file__).resolve().parent
    s = str(here)
    if s not in sys.path:
        sys.path.insert(0, s)


def register(ctx: Any) -> None:
    """Plugin entry point — called by ``hermes_cli.plugins.PluginManager._load_plugin``."""
    _ensure_package_on_path()

    data_dir = _resolve_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    backend = _resolve_backend()
    db_filename = _resolve_db_filename(backend)
    db_path = data_dir / db_filename

    from hermes_inspector.store import make_store, set_store

    store = make_store(db_path, backend=backend)
    store.init()
    set_store(store)
    log.info(
        "hermes-inspector ready (backend=%s, data=%s)",
        backend,
        db_path,
    )

    # Subscribe to the REAL Hermes hooks. ``kanban_task_claimed`` /
    # ``kanban_task_completed`` / ``kanban_task_blocked`` are the only
    # kanban lifecycle hooks the agent core fires today; we translate
    # them into inspector rows in real time.
    from hermes_inspector import hooks

    ctx.register_hook("kanban_task_claimed", hooks.on_kanban_task_claimed)
    ctx.register_hook("kanban_task_completed", hooks.on_kanban_task_completed)
    ctx.register_hook("kanban_task_blocked", hooks.on_kanban_task_blocked)

    # Register the doc-emission tool. Hermes core has no ``doc_emitted``
    # hook; agents call this tool when they want a doc captured.
    from hermes_inspector.tool import register as register_tool

    register_tool(ctx)


__all__ = ["register"]