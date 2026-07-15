"""``inspector_emit_doc`` tool — agents call this to capture a doc emission.

Hermes core does not currently expose a ``doc_emitted`` lifecycle hook,
so the canonical way for an agent to push a doc into the inspector is
through this tool. The handler delegates to ``hooks.emit_doc`` so the
persistence path is shared with every other entry point.
"""
from __future__ import annotations

from typing import Any, Dict

from hermes_inspector import hooks
from hermes_inspector.store import get_store


SPEC: Dict[str, Any] = {
    "name": "inspector_emit_doc",
    "description": (
        "Capture a doc Hermes is about to emit (PR summary, brief, ADR, "
        "note) into the inspector's persistent store. The doc is "
        "associated with a kanban task id so the dashboard can group "
        "emissions by their originating task."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "Optional explicit doc id; one is generated if omitted.",
            },
            "task_id": {
                "type": "string",
                "description": "Kanban task id the doc belongs to (required).",
            },
            "title": {
                "type": "string",
                "description": "Human-readable title; surfaced in the dashboard list.",
            },
            "content": {
                "type": "string",
                "description": "Doc body (markdown / raw text).",
                "default": "",
            },
            "source": {
                "type": "string",
                "description": "Doc provenance tag — 'pr-summary' | 'brief' | 'adr' | 'note' | ...",
                "default": "note",
            },
            "completed_at": {
                "type": "string",
                "description": "Optional ISO-8601 timestamp; NULL while the doc is a draft.",
            },
        },
        "required": ["task_id", "title"],
    },
}


def handler(**kwargs: Any) -> Dict[str, Any]:
    """Tool handler — calls ``hooks.emit_doc`` for shared validation + persistence."""
    try:
        # ``get_store()`` will raise if the plugin hasn't been registered
        # yet; surface that as a structured error so the model can react.
        get_store()
    except RuntimeError as exc:
        return {"status": "error", "error": str(exc)}
    # The tool schema exposes ``id`` (short, matches the column name);
    # the hook callable uses ``doc_id`` (matches the JS store API). Map
    # between them so callers don't need to know which name to use.
    if "id" in kwargs and "doc_id" not in kwargs:
        kwargs["doc_id"] = kwargs.pop("id")
    return hooks.emit_doc(**kwargs)


def register(ctx: Any) -> None:
    """Register this tool with a Hermes plugin context."""
    ctx.register_tool(
        name=SPEC["name"],
        toolset="inspector",
        schema=SPEC,
        handler=handler,
        description=SPEC["description"],
    )


__all__ = ["SPEC", "handler", "register"]