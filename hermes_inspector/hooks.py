"""Lifecycle hook handlers for the Hermes Inspector plugin.

The plugin subscribes to the REAL Hermes hooks
(``kanban_task_claimed`` / ``kanban_task_completed`` / ``kanban_task_blocked``)
and translates them into inspector rows. Doc emissions are captured via the
``inspector_emit_doc`` tool registered from ``__init__.py`` because Hermes
core does not currently emit a ``doc_emitted`` hook.

All functions here are safe to call from the agent loop. A hook that throws
would tear down the dispatcher, so every handler is wrapped in a
try/except that logs and returns a structured status dict.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from hermes_inspector.store import get_store

log = logging.getLogger(__name__)

VALID_COLUMNS = {"todo", "ready", "running", "blocked", "review", "done"}


def _ok(note: str = "") -> Dict[str, str]:
    return {"status": "ok", "note": note}


def _coerce_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def _coerce_parents(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return [value]
    return []


# ---------------------------------------------------------------------------
# Hook handlers — bound to actual Hermes hook names in register(ctx).
# ---------------------------------------------------------------------------


def on_kanban_task_claimed(
    *,
    task_id: Any = None,
    title: Any = None,
    body: Any = None,
    assignee: Any = None,
    parents: Any = None,
    **_: Any,
) -> Dict[str, str]:
    """``kanban_task_claimed`` -> ``task_created`` translation.

    The dispatcher fires this hook in the WORKER process right before the
    worker subprocess spawns. A slow / failing hook here would block
    dispatch, so we wrap the body in try/except.
    """
    try:
        cid = _coerce_str(task_id)
        if not cid:
            return _ok("ignored: missing task_id")
        get_store().upsert_card({
            "card_id": cid,
            "title": _coerce_str(title, default=cid),
            "body": _coerce_str(body),
            "column": "ready",
            "assignee": _coerce_str(assignee) or None,
            "parents": _coerce_parents(parents),
        })
        return _ok()
    except Exception as exc:  # noqa: BLE001 — hook must never raise
        log.warning("inspector.on_kanban_task_claimed failed: %s", exc, exc_info=True)
        return {"status": "error", "error": str(exc)}


def on_kanban_task_completed(
    *,
    task_id: Any = None,
    summary: Any = None,
    title: Any = None,
    body: Any = None,
    assignee: Any = None,
    parents: Any = None,
    **_: Any,
) -> Dict[str, str]:
    """``kanban_task_completed`` -> ``task_completed`` translation."""
    try:
        cid = _coerce_str(task_id)
        if not cid:
            return _ok("ignored: missing task_id")
        existing = get_store().get_card(cid)
        # If we missed the claim event, create the row on the fly.
        card = {
            "card_id": cid,
            "title": _coerce_str(title) or (existing["title"] if existing else cid),
            "body": _coerce_str(body) or (existing["body"] if existing else ""),
            "column": "done",
            "assignee": _coerce_str(assignee) or (existing["assignee"] if existing else None),
            "parents": _coerce_parents(parents) or (existing["parents"] if existing else []),
        }
        if existing and (summary := _coerce_str(summary)):
            # Append the completion summary to the body so the dashboard
            # shows the outcome without a separate event row.
            sep = "\n\n" if card["body"] else ""
            card["body"] = f"{card['body']}{sep}completed: {summary}"
        get_store().upsert_card(card)
        return _ok()
    except Exception as exc:  # noqa: BLE001
        log.warning("inspector.on_kanban_task_completed failed: %s", exc, exc_info=True)
        return {"status": "error", "error": str(exc)}


def on_kanban_task_blocked(
    *,
    task_id: Any = None,
    reason: Any = None,
    title: Any = None,
    body: Any = None,
    assignee: Any = None,
    parents: Any = None,
    **_: Any,
) -> Dict[str, str]:
    """``kanban_task_blocked`` -> ``task_failed`` translation."""
    try:
        cid = _coerce_str(task_id)
        if not cid:
            return _ok("ignored: missing task_id")
        existing = get_store().get_card(cid)
        card = {
            "card_id": cid,
            "title": _coerce_str(title) or (existing["title"] if existing else cid),
            "body": _coerce_str(body) or (existing["body"] if existing else ""),
            "column": "blocked",
            "assignee": _coerce_str(assignee) or (existing["assignee"] if existing else None),
            "parents": _coerce_parents(parents) or (existing["parents"] if existing else []),
        }
        if reason := _coerce_str(reason):
            sep = "\n\n" if card["body"] else ""
            card["body"] = f"{card['body']}{sep}blocked: {reason}"
        get_store().upsert_card(card)
        return _ok()
    except Exception as exc:  # noqa: BLE001
        log.warning("inspector.on_kanban_task_blocked failed: %s", exc, exc_info=True)
        return {"status": "error", "error": str(exc)}


def emit_doc(
    *,
    doc_id: Any = None,
    task_id: Any = None,
    title: Any = None,
    content: Any = None,
    source: Any = None,
    completed_at: Any = None,
    **_: Any,
) -> Dict[str, str]:
    """Persist a doc row. Used by the ``inspector_emit_doc`` tool."""
    try:
        cid = _coerce_str(task_id)
        if not cid:
            return {"status": "error", "error": "task_id is required"}
        result = get_store().save_doc({
            "id": _coerce_str(doc_id) or None,
            "task_id": cid,
            "title": _coerce_str(title, default="(untitled doc)"),
            "content": _coerce_str(content),
            "source": _coerce_str(source, default="note"),
            "completed_at": completed_at,
        })
        return {"status": "ok", "doc_id": result["id"]}
    except Exception as exc:  # noqa: BLE001
        log.warning("inspector.emit_doc failed: %s", exc, exc_info=True)
        return {"status": "error", "error": str(exc)}


# Mapping used by ``register(ctx)`` so the dispatch surface stays
# declarative and easy to audit.
HOOK_NAMES = (
    "kanban_task_claimed",
    "kanban_task_completed",
    "kanban_task_blocked",
)


__all__ = [
    "HOOK_NAMES",
    "VALID_COLUMNS",
    "on_kanban_task_claimed",
    "on_kanban_task_completed",
    "on_kanban_task_blocked",
    "emit_doc",
]