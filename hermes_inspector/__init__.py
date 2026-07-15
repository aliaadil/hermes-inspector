"""Hermes Inspector — Python source package.

Implements the supported current-Hermes plugin contract:

* storage layer for emitted docs + kanban snapshots
  (SQLite primary, JSON-file fallback for environments without sqlite)
* lifecycle hooks that subscribe to the REAL Hermes hooks
  (kanban_task_claimed / kanban_task_completed / kanban_task_blocked)
  and translate them into inspector rows.
* an ``inspector_emit_doc`` tool agents call to capture emitted docs.
* a FastAPI ``router`` consumed by the dashboard plugin manifest.

The whole package is shipped at the repo root so that
``hermes plugins install aliaadil/hermes-inspector`` clones a single
repository that is simultaneously a valid general Hermes plugin
(``plugin.yaml`` + ``__init__.py`` at the root) and a dashboard
extension (``dashboard/manifest.json`` + ``dashboard/plugin_api.py``).
"""
from __future__ import annotations

__version__ = "1.0.0"
__all__ = ["__version__"]