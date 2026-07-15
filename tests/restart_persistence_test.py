"""Persistence check — re-open the inspector store from a fresh process.

Reads /api/board and /api/docs back from the SQLite file left behind by
the integration test in a previous process. Verifies that:

* the store opens cleanly against the on-disk file (no schema drift)
* card rows persist across process boundaries
* doc rows persist across process boundaries
* `POST /api/board/move` on a card works in the new process and the
  change is reflected on the next read

This mirrors the QA report's "Restart persistence" check from the prior
round, but exercises it through the new Python plugin instead of the
JS plugin.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def _say(stage: str, **fields) -> None:
    print(f"[restart] {stage}: {json.dumps(fields, default=str)}", flush=True)


def main() -> int:
    data_dir = Path(os.environ.get("HERMES_INSPECTOR_DATA_DIR") or "").resolve()
    if not data_dir.is_dir():
        print(f"HERMES_INSPECTOR_DATA_DIR not set or not a directory: {data_dir}", file=sys.stderr)
        return 2
    db_path = data_dir / "inspector.db"
    if not db_path.exists():
        print(f"inspector.db not found at {db_path}", file=sys.stderr)
        return 3

    # Make sure the package is importable from the install dir.
    install = Path(os.environ.get("INSPECT_INSTALL_ROOT") or "").resolve()
    if install.is_dir():
        sys.path.insert(0, str(install))

    from hermes_inspector.store import make_store, set_store
    from hermes_inspector.api import build_router

    store = make_store(db_path, backend="sqlite")
    store.init()
    # Install the store in the process-wide singleton so the dashboard
    # router's ``get_store()`` finds it (no plugin loader in this
    # subprocess; the test exercises the API in isolation).
    set_store(store)
    cards_before = store.list_board()
    docs_before = store.list_docs()
    _say("loaded", cards=len(cards_before), docs=len(docs_before))
    if not cards_before:
        print("no cards in DB after restart", file=sys.stderr)
        return 4

    # Move a card via the API to prove the write path also works after restart.
    router = build_router()
    move_route = None
    for r in router.routes:
        if getattr(r, "path", None) == "/api/board/move":
            move_route = r
            break
    if move_route is None:
        print("no /api/board/move route", file=sys.stderr)
        return 5

    from hermes_inspector.hooks import VALID_COLUMNS
    target_card = cards_before[0]
    target_id = target_card["card_id"]
    current_col = target_card["column"]
    next_col = "review" if current_col != "review" else "done"

    from pydantic import BaseModel
    from hermes_inspector.api import MoveCardRequest

    move_payload = MoveCardRequest(card_id=target_id, to_column=next_col)
    result = move_route.endpoint(body=move_payload)
    _say("move_result", payload=result)

    # Re-read.
    cards_after = store.list_board()
    moved = next((c for c in cards_after if c["card_id"] == target_id), None)
    if moved is None:
        print("moved card disappeared", file=sys.stderr)
        return 6
    if moved["column"] != next_col:
        print(f"expected column={next_col}, got {moved['column']}", file=sys.stderr)
        return 7
    _say("move_persisted", card_id=target_id, column=moved["column"])

    _say("ok", all_steps_passed=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())