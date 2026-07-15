"""Tests for the lifecycle hook handlers.

These tests exercise the real Hermes hook contract:
``kanban_task_claimed`` / ``kanban_task_completed`` / ``kanban_task_blocked``
from ``hermes_cli.kanban_db``. The handlers translate those into the
inspector's internal events (task_created / task_completed / task_failed)
so the dashboard can show the same row set regardless of which hook
fired it.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parents[1]))

from tests.helpers import TempDirMixin  # noqa: E402

from hermes_inspector import hooks  # noqa: E402
from hermes_inspector.store import make_store, set_store  # noqa: E402


class HooksTests(TempDirMixin, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = make_store(self.tmp_dir / "inspector.db", backend="sqlite")
        self.store.init()
        set_store(self.store)

    def tearDown(self) -> None:
        set_store(None)
        self.store.close()
        super().tearDown()

    # ---- claim (translates to task_created) -------------------------------

    def test_kanban_task_claimed_creates_card_in_ready(self) -> None:
        result = hooks.on_kanban_task_claimed(task_id="t_x", title="Hello", body="world")
        self.assertEqual(result["status"], "ok")
        card = self.store.get_card("t_x")
        self.assertIsNotNone(card)
        self.assertEqual(card["column"], "ready")
        self.assertEqual(card["title"], "Hello")
        self.assertEqual(card["body"], "world")

    def test_kanban_task_claimed_picks_up_assignee_and_parents(self) -> None:
        hooks.on_kanban_task_claimed(
            task_id="t_y",
            title="Child",
            body="",
            assignee="builder",
            parents=["t_parent"],
        )
        card = self.store.get_card("t_y")
        self.assertEqual(card["assignee"], "builder")
        self.assertEqual(card["parents"], ["t_parent"])

    def test_kanban_task_claimed_is_idempotent(self) -> None:
        hooks.on_kanban_task_claimed(task_id="t_z", title="first", body="")
        hooks.on_kanban_task_claimed(task_id="t_z", title="second", body="updated body")
        card = self.store.get_card("t_z")
        self.assertEqual(card["title"], "second")
        self.assertEqual(card["body"], "updated body")
        # Still in ready — claim does not advance the column.
        self.assertEqual(card["column"], "ready")

    # ---- complete ---------------------------------------------------------

    def test_kanban_task_completed_moves_card_to_done(self) -> None:
        hooks.on_kanban_task_claimed(task_id="t_q", title="Q", body="")
        result = hooks.on_kanban_task_completed(task_id="t_q", summary="Shipped")
        self.assertEqual(result["status"], "ok")
        card = self.store.get_card("t_q")
        self.assertEqual(card["column"], "done")

    def test_kanban_task_completed_without_prior_claim_still_creates_card(self) -> None:
        # Edge case: completion event arrives before claim (e.g. backlog import).
        hooks.on_kanban_task_completed(task_id="t_orphan", summary="done")
        card = self.store.get_card("t_orphan")
        self.assertIsNotNone(card)
        self.assertEqual(card["column"], "done")
        self.assertEqual(card["title"], "t_orphan")

    # ---- block (translates to task_failed) --------------------------------

    def test_kanban_task_blocked_moves_card_to_blocked(self) -> None:
        hooks.on_kanban_task_claimed(task_id="t_b", title="B", body="")
        result = hooks.on_kanban_task_blocked(task_id="t_b", reason="needs human")
        self.assertEqual(result["status"], "ok")
        card = self.store.get_card("t_b")
        self.assertEqual(card["column"], "blocked")
        self.assertIn("needs human", card["body"])

    # ---- emit_doc ---------------------------------------------------------

    def test_emit_doc_writes_doc_row(self) -> None:
        result = hooks.emit_doc(
            doc_id="doc_e2e",
            task_id="t_1",
            title="Brief",
            content="hello",
            source="brief",
        )
        self.assertEqual(result["status"], "ok")
        row = self.store.get_doc("doc_e2e")
        self.assertEqual(row["title"], "Brief")
        self.assertEqual(row["content"], "hello")

    def test_emit_doc_generates_id_when_omitted(self) -> None:
        result = hooks.emit_doc(task_id="t_1", title="x", content="", source="note")
        self.assertIn("doc_id", result)
        self.assertTrue(result["doc_id"].startswith("doc_"))

    # ---- robustness -------------------------------------------------------

    def test_hooks_survive_bad_payload(self) -> None:
        # Garbage must NOT raise — the agent loop would be killed by a hook
        # that throws.
        result = hooks.on_kanban_task_claimed(task_id=None, title=None, body=None)
        # Either ignored or coerced; in both cases we return ok with a note.
        self.assertEqual(result["status"], "ok")
        self.assertIn("note", result)


if __name__ == "__main__":
    unittest.main()