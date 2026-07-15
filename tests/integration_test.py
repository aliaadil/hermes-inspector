"""Integration test against the REAL Hermes v0.18 plugin contract.

This script runs INSIDE a hermes-runtime subprocess, against an
isolated HERMES_HOME that has just had `hermes plugins install
aliaadil/hermes-inspector --enable` run against it. It exercises the
plugin through the actual mechanisms Hermes uses:

1. Force plugin discovery (PluginManager.discover_and_load) — verifies
   `register(ctx)` runs without errors.
2. Trigger kanban lifecycle transitions via real kanban_db calls —
   verifies the three real Hermes hooks fire and produce inspector
   rows.
3. Invoke the registered `inspector_emit_doc` tool through the
   plugin's tool table — verifies the tool path works end-to-end.
4. Read back rows through the dashboard FastAPI router — verifies
   the API surfaces everything the hooks persisted.

This is the "real Hermes plugin contract" coverage the QA report
asked for: it imports the *installed* plugin under the *real*
PluginManager and drives it through the real hook-firing path,
without faking the dispatcher or substituting JS imports.

Run from a hermes shell:

    HERMES_HOME=<isolated> MOCK_AUTH=true \
        /opt/hermes/.venv/bin/python tests/integration_test.py
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path


def _say(stage: str, **fields) -> None:
    print(f"[integration] {stage}: {json.dumps(fields, default=str)}", flush=True)


def main() -> int:
    hermes_home = Path(os.environ.get("HERMES_HOME") or "").resolve()
    if not hermes_home.is_dir():
        print(f"HERMES_HOME not set or not a directory: {hermes_home}", file=sys.stderr)
        return 2

    plugins_dir = hermes_home / "plugins"
    install_root = plugins_dir / "hermes-inspector"
    if not (install_root / "plugin.yaml").exists():
        print(f"plugin.yaml not found at {install_root}", file=sys.stderr)
        return 2
    if not (install_root / "__init__.py").exists():
        print(f"__init__.py not found at {install_root}", file=sys.stderr)
        return 2

    # Track every task this test creates so we can archive them on exit,
    # even on early-return failure paths. Without this, each test run
    # leaves a "integration: claimed" and "integration: blocked" card in
    # the user's real board when HERMES_HOME points at the live DB
    # instead of a throwaway mktemp dir.
    created_task_ids: list[str] = []

    # Pin the inspector store inside the isolated HERMES_HOME so we
    # don't accidentally write into the install dir.
    data_dir = hermes_home / "inspector-data"
    data_dir.mkdir(exist_ok=True)
    os.environ["HERMES_INSPECTOR_DATA_DIR"] = str(data_dir)
    os.environ.setdefault("HERMES_INSPECTOR_BACKEND", "sqlite")

    # 1) Force real plugin discovery under the real PluginManager.
    _say("step", step=1, action="discover_plugins")
    from hermes_cli.plugins import discover_plugins, get_plugin_manager

    try:
        discover_plugins(force=True)
    except Exception:
        traceback.print_exc()
        return 3

    manager = get_plugin_manager()
    loaded = manager._plugins.get("hermes-inspector")
    if loaded is None:
        for key, val in manager._plugins.items():
            if val.manifest.name == "hermes-inspector":
                loaded = val
                break
    if loaded is None:
        print(
            "hermes-inspector not discovered by real PluginManager. "
            "Check that plugins.enabled contains 'hermes-inspector'.",
            file=sys.stderr,
        )
        return 4
    if loaded.error:
        print(f"plugin load error: {loaded.error}", file=sys.stderr)
        return 5
    _say(
        "plugin_loaded",
        tools=loaded.tools_registered,
        hooks=loaded.hooks_registered,
    )

    expected_hooks = {"kanban_task_claimed", "kanban_task_completed", "kanban_task_blocked"}
    missing = expected_hooks - set(loaded.hooks_registered)
    if missing:
        print(f"missing hooks: {sorted(missing)}", file=sys.stderr)
        return 6

    expected_tools = {"inspector_emit_doc"}
    missing_tools = expected_tools - set(loaded.tools_registered)
    if missing_tools:
        print(f"missing tools: {sorted(missing_tools)}", file=sys.stderr)
        return 7

    # 2) Exercise the real kanban lifecycle hooks via the actual
    # hermes_cli.kanban_db transitions. The kanban DB lives in HERMES_HOME.
    _say("step", step=2, action="kanban_lifecycle")
    from hermes_cli import kanban_db as kb
    from hermes_inspector.store import get_store

    board = "default"

    # Run the rest of the test inside a try/finally so created tasks get
    # archived no matter how we exit (return N, exception, KeyboardInterrupt).
    try:
        return _run_lifecycle_and_tool_checks(
            board=board,
            kb=kb,
            get_store=get_store,
            created_task_ids=created_task_ids,
        )
    finally:
        _archive_created_tasks(board=board, kb=kb, task_ids=created_task_ids)


def _create_task(
    *, kb, board: str, created_task_ids: list[str], **kwargs
) -> str:
    """Create a task and append its id to ``created_task_ids``.

    The append happens immediately after ``create_task`` returns, so the
    id is always tracked if the row is in the DB. ``create_task`` is
    atomic: if it raises, the row was rolled back and there's nothing to
    clean up — we let the exception propagate so the assertion checks
    downstream see the real failure mode.
    """
    with kb.connect_closing(board=board) as conn:
        task_id = kb.create_task(conn, **kwargs)
    created_task_ids.append(task_id)
    return task_id


def _run_lifecycle_and_tool_checks(
    *,
    board: str,
    kb,
    get_store,
    created_task_ids: list[str],
) -> int:
    """Steps 2-4 of the integration test. Returns the exit code.

    Every task created via ``_create_task`` is appended to
    ``created_task_ids`` so the caller can archive it on the way out.
    """

    # Create task A; claim it -> kanban_task_claimed hook should run.
    task_a_id = _create_task(
        kb=kb,
        board=board,
        created_task_ids=created_task_ids,
        title="integration: claimed",
        body="triggering kanban_task_claimed via real kanban_db",
        assignee="builder",
    )
    _say("kanban_create", task_id=task_a_id)
    with kb.connect_closing(board=board) as conn:
        kb.claim_task(conn, task_a_id, claimer="integration-test")

    store = get_store()
    card = store.get_card(task_a_id)
    if not card:
        print("kanban_task_claimed hook did not produce a card row", file=sys.stderr)
        return 8
    _say("post_claim_card", column=card.get("column"), title=card.get("title"))

    # Complete it -> kanban_task_completed hook.
    with kb.connect_closing(board=board) as conn:
        kb.complete_task(conn, task_a_id, summary="integration completion")
    card = store.get_card(task_a_id)
    _say("post_complete_card", column=card.get("column"), body_excerpt=(card.get("body") or "")[:60])
    if card.get("column") != "done":
        print(f"expected column=done after complete, got {card.get('column')}", file=sys.stderr)
        return 9

    # Block task B -> kanban_task_blocked hook.
    task_b_id = _create_task(
        kb=kb,
        board=board,
        created_task_ids=created_task_ids,
        title="integration: blocked",
        body="triggering kanban_task_blocked via real kanban_db",
        assignee="builder",
    )
    with kb.connect_closing(board=board) as conn:
        kb.block_task(conn, task_b_id, reason="integration test blocker")
    card = store.get_card(task_b_id)
    _say("post_block_card", column=card.get("column"), body_excerpt=(card.get("body") or "")[:60])
    if card.get("column") != "blocked":
        print(f"expected column=blocked, got {card.get('column')}", file=sys.stderr)
        return 10

    # 3) Invoke the registered tool by name through the real plugin
    # tool table to capture a doc emission.
    _say("step", step=3, action="tool_invocation")
    from tools.registry import registry

    entry = registry.get_entry("inspector_emit_doc")
    if entry is None:
        print("inspector_emit_doc not in tools registry", file=sys.stderr)
        return 11
    result = entry.handler(
        task_id=task_a_id,
        title="integration: doc emitted",
        content="emitted through the real registered tool path",
        source="integration-test",
    )
    _say("tool_result", result=result)
    docs = store.list_docs(task_id=task_a_id)
    if not docs:
        print("tool invocation did not produce a doc row", file=sys.stderr)
        return 12
    _say("doc_count", count=len(docs))

    # 4) Read everything back through the dashboard FastAPI router.
    # TestClient has a known starlette/fastapi version mismatch in this
    # env (AssertionError: fastapi_middleware_astack not found in request
    # scope), so we exercise the router functions directly — same code
    # path, just no TestClient transport.
    _say("step", step=4, action="api_roundtrip")
    from hermes_inspector.api import build_router

    router = build_router()

    # Drive each route function in-process and verify status + payload.
    def _route(path: str):
        for r in router.routes:
            if getattr(r, "path", None) == path:
                return r
        return None

    board_route = _route("/api/board")
    docs_route = _route("/api/docs")
    health_route = _route("/health")
    if board_route is None or docs_route is None:
        print(f"missing API route: board={board_route}, docs={docs_route}", file=sys.stderr)
        return 13

    class _FakeReq:
        pass

    # Route functions were declared with FastAPI dependency-injected
    # ``request: Request`` and Query defaults. Call them with the args
    # the underlying endpoint expects.
    board_payload = board_route.endpoint()
    docs_payload = docs_route.endpoint(
        request=_FakeReq(), task_id=None, since=None, limit=100
    )
    # Pydantic v2 models: use attribute access, not .get().
    board_cards = list(board_payload.cards)
    docs_count = int(docs_payload.count)
    _say(
        "api_roundtrip",
        board_cards=len(board_cards),
        docs_count=docs_count,
    )
    if len(board_cards) < 2:
        print("board API did not return both cards", file=sys.stderr)
        return 14
    if docs_count < 1:
        print("docs API did not return the captured doc", file=sys.stderr)
        return 15

    # Health endpoint is optional — verify if present.
    if health_route is not None:
        try:
            health_payload = health_route.endpoint()
            _say("health", payload=health_payload)
        except Exception:
            pass

    _say("ok", all_steps_passed=True)
    return 0


def _archive_created_tasks(*, board: str, kb, task_ids: list[str]) -> None:
    """Archive every task id the test created, best-effort.

    Runs from the caller's ``finally`` so cleanup happens on success,
    early-return failure, and exceptions. Each ``archive_task`` call
    uses its own short-lived connection because the test may have
    crashed mid-transaction in a way that left the previous connection
    in an unusable state.

    Archive is idempotent (returns False if the task is already gone or
    not in an archivable state), so a partially-completed run won't
    raise on the second pass.
    """
    for tid in task_ids:
        try:
            with kb.connect_closing(board=board) as conn:
                archived = kb.archive_task(conn, tid)
            _say("teardown_archive", task_id=tid, archived=bool(archived))
        except Exception as exc:
            # Never let cleanup mask the real test result.
            _say(
                "teardown_archive_failed",
                task_id=tid,
                error=f"{type(exc).__name__}: {exc}",
            )


if __name__ == "__main__":
    sys.exit(main())