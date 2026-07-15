"""FastAPI router for the Hermes Inspector dashboard plugin.

This module is consumed two ways:

1. **Direct import** by the dashboard's auto-discovery. The
   ``dashboard/plugin_api.py`` shim re-exports ``router`` from here so
   ``hermes_cli.web_server._mount_plugin_api_routes`` can mount it
   under ``/api/plugins/hermes-inspector/``.

2. **Test surface** via ``build_router()``, which returns a fresh
   APIRouter instance wired against the process-wide store.

All endpoints are session-protected at the dashboard layer — no
auth lives here.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from fastapi import APIRouter, HTTPException, Query, Request
    from pydantic import BaseModel, Field
except Exception:  # pragma: no cover - tests inject stand-ins if fastapi missing
    APIRouter = None  # type: ignore
    HTTPException = Exception  # type: ignore
    Query = None  # type: ignore
    Request = object  # type: ignore
    BaseModel = object  # type: ignore

    def Field(*_args, **_kwargs):  # type: ignore
        return None

from hermes_inspector.store import get_store
from hermes_inspector.hooks import VALID_COLUMNS

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):  # type: ignore[misc]
    status: str
    plugin: str
    version: str


class DocSummary(BaseModel):  # type: ignore[misc]
    id: str
    task_id: str
    title: str
    source: str
    created_at: str
    completed_at: Optional[str] = None


class DocListResponse(BaseModel):  # type: ignore[misc]
    docs: List[DocSummary]
    count: int


class DocDetailResponse(BaseModel):  # type: ignore[misc]
    id: str
    task_id: str
    title: str
    content: str
    source: str
    created_at: str
    completed_at: Optional[str] = None


class SaveDocRequest(BaseModel):  # type: ignore[misc]
    id: Optional[str] = None
    task_id: str
    title: str
    content: str = ""
    source: str = "note"
    completed_at: Optional[str] = None


class SaveDocResponse(BaseModel):  # type: ignore[misc]
    status: str
    doc_id: str


class CardSummary(BaseModel):  # type: ignore[misc]
    card_id: str
    title: str
    body: str
    column: str
    parents: List[str] = Field(default_factory=list)
    assignee: Optional[str] = None
    created_at: str
    updated_at: str


class BoardResponse(BaseModel):  # type: ignore[misc]
    cards: List[CardSummary]
    count: int


class MoveCardRequest(BaseModel):  # type: ignore[misc]
    card_id: str
    to_column: str


class MoveCardResponse(BaseModel):  # type: ignore[misc]
    status: str
    card_id: str
    column: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$"
)


def parse_since(raw: Optional[str]) -> Optional[str]:
    """Normalize a ``?since=`` filter value to ISO-8601 or ``None``.

    Accepts either an ISO-8601 string (returned unchanged) or an
    epoch-milliseconds number (converted to ISO). Returns ``None``
    when the input is missing or unparseable.
    """
    if not raw:
        return None
    if _ISO_RE.match(raw):
        return raw
    try:
        ms = int(raw)
    except ValueError:
        try:
            # Allow plain seconds too — JS callers sometimes send those.
            ms = int(float(raw) * 1000)
        except ValueError:
            return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return None


def _docs_filtered(query, store_rows):
    """Apply ``?task_id`` and ``?since`` filters client-side (JSON backend has no SQL)."""
    rows = list(store_rows)
    task_id = getattr(query, "task_id", None) if query is not None else None
    since = getattr(query, "since", None) if query is not None else None
    if task_id:
        rows = [r for r in rows if r.get("task_id") == task_id]
    if since:
        rows = [r for r in rows if (r.get("created_at") or "") >= since]
    return rows


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


PLUGIN_NAME = "hermes-inspector"
PLUGIN_VERSION = "1.0.0"


def build_router() -> Any:
    """Construct and return a fresh APIRouter for this plugin."""
    if APIRouter is None:
        raise RuntimeError(
            "fastapi is not installed; install fastapi to mount the inspector dashboard plugin"
        )
    router = APIRouter()

    @router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", plugin=PLUGIN_NAME, version=PLUGIN_VERSION)

    @router.get("/api/docs", response_model=DocListResponse)
    def list_docs(
        request: Request,
        task_id: Optional[str] = Query(default=None),
        since: Optional[str] = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> DocListResponse:
        rows = get_store().list_docs(task_id=task_id, limit=limit)
        if since:
            since_iso = parse_since(since)
            if since_iso:
                rows = [r for r in rows if (r.get("created_at") or "") >= since_iso]
        docs = [DocSummary(**{k: r.get(k) for k in DocSummary.model_fields.keys() if k in r}) for r in rows]
        return DocListResponse(docs=docs, count=len(docs))

    @router.get("/api/docs/{doc_id}", response_model=DocDetailResponse)
    def get_doc(doc_id: str) -> DocDetailResponse:
        row = get_store().get_doc(doc_id)
        if not row:
            raise HTTPException(status_code=404, detail=f"doc {doc_id!r} not found")
        return DocDetailResponse(**row)

    @router.post("/api/docs", response_model=SaveDocResponse)
    def save_doc(body: SaveDocRequest) -> SaveDocResponse:
        result = get_store().save_doc(body.model_dump(exclude_none=True))
        return SaveDocResponse(status="ok", doc_id=result["id"])

    @router.get("/api/board", response_model=BoardResponse)
    def list_board() -> BoardResponse:
        rows = get_store().list_board()
        cards = []
        for r in rows:
            r = dict(r)
            r.setdefault("parents", [])
            cards.append(CardSummary(**{k: r.get(k) for k in CardSummary.model_fields.keys() if k in r}))
        return BoardResponse(cards=cards, count=len(cards))

    @router.post("/api/board/move", response_model=MoveCardResponse)
    def move_card(body: MoveCardRequest) -> MoveCardResponse:
        if body.to_column not in VALID_COLUMNS:
            raise HTTPException(
                status_code=422,
                detail=f"to_column must be one of {sorted(VALID_COLUMNS)}",
            )
        result = get_store().move_card(body.card_id, body.to_column)
        if not result:
            raise HTTPException(status_code=404, detail=f"card {body.card_id!r} not found")
        return MoveCardResponse(status="ok", card_id=result["card_id"], column=result["column"])

    return router


# Singleton router — the dashboard import resolves ``router`` from this module.
router = build_router() if APIRouter is not None else None  # type: ignore[assignment]


__all__ = [
    "router",
    "build_router",
    "parse_since",
    "PLUGIN_NAME",
    "PLUGIN_VERSION",
    "HealthResponse",
    "DocSummary",
    "DocListResponse",
    "DocDetailResponse",
    "SaveDocRequest",
    "SaveDocResponse",
    "CardSummary",
    "BoardResponse",
    "MoveCardRequest",
    "MoveCardResponse",
]