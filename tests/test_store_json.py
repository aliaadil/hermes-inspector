"""Tests for the JSON-file storage backend.

The JSON backend is used when SQLite is unavailable (rare in practice
but it keeps the plugin installable on locked-down systems). API and
behavior must match the SQLite backend so consumers don't care which
backend is active.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parents[1]))

from tests.helpers import TempDirMixin  # noqa: E402

from hermes_inspector.store_json import JsonStore  # noqa: E402


class JsonStoreInitTests(TempDirMixin, unittest.TestCase):
    def test_init_creates_file(self) -> None:
        path = self.tmp_dir / "inspector.json"
        store = JsonStore(path)
        store.init()
        self.assertTrue(path.exists())
        store.close()

    def test_init_is_idempotent(self) -> None:
        path = self.tmp_dir / "inspector.json"
        store = JsonStore(path)
        store.init()
        store.init()
        store.close()

    def test_init_creates_parent_directory(self) -> None:
        path = self.tmp_dir / "nested" / "inspector.json"
        JsonStore(path).init()
        self.assertTrue(path.exists())


class JsonStoreDocTests(TempDirMixin, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = JsonStore(self.tmp_dir / "inspector.json")
        self.store.init()

    def tearDown(self) -> None:
        self.store.close()
        super().tearDown()

    def test_save_doc_generates_id_when_omitted(self) -> None:
        result = self.store.save_doc({
            "task_id": "t_001",
            "title": "Brief",
            "content": "hello",
            "source": "brief",
        })
        self.assertTrue(result["id"].startswith("doc_"))

    def test_save_doc_upserts_on_conflict(self) -> None:
        self.store.save_doc({"id": "doc_x", "task_id": "t_001", "title": "V1", "content": "first", "source": "note"})
        self.store.save_doc({"id": "doc_x", "task_id": "t_002", "title": "V2", "content": "second", "source": "note"})
        row = self.store.get_doc("doc_x")
        self.assertEqual(row["title"], "V2")
        self.assertEqual(row["task_id"], "t_002")

    def test_get_doc_returns_none_for_unknown(self) -> None:
        self.assertIsNone(self.store.get_doc("doc_does_not_exist"))

    def test_list_docs_returns_recent_first(self) -> None:
        for i in range(3):
            self.store.save_doc({"id": f"doc_l_{i}", "task_id": "t_001", "title": f"d{i}", "content": "", "source": "note"})
        rows = self.store.list_docs()
        ids = [r["id"] for r in rows]
        self.assertEqual(ids, sorted(ids, reverse=True))

    def test_list_docs_filters_by_task_id(self) -> None:
        self.store.save_doc({"id": "doc_a", "task_id": "t_1", "title": "a", "content": "", "source": "note"})
        self.store.save_doc({"id": "doc_b", "task_id": "t_2", "title": "b", "content": "", "source": "note"})
        rows = self.store.list_docs(task_id="t_1")
        self.assertEqual([r["id"] for r in rows], ["doc_a"])


class JsonStoreCardTests(TempDirMixin, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = JsonStore(self.tmp_dir / "inspector.json")
        self.store.init()

    def tearDown(self) -> None:
        self.store.close()
        super().tearDown()

    def test_upsert_and_get_card(self) -> None:
        self.store.upsert_card({
            "card_id": "t_card_1",
            "title": "Build X",
            "column": "ready",
            "parents": ["t_parent_1"],
            "assignee": "builder",
        })
        row = self.store.get_card("t_card_1")
        self.assertEqual(row["title"], "Build X")
        self.assertEqual(row["parents"], ["t_parent_1"])

    def test_move_card_changes_column(self) -> None:
        self.store.upsert_card({"card_id": "t_card_3", "title": "x", "column": "ready"})
        result = self.store.move_card("t_card_3", "done")
        self.assertEqual(result, {"card_id": "t_card_3", "column": "done"})

    def test_move_card_returns_none_for_unknown(self) -> None:
        self.assertIsNone(self.store.move_card("t_nope", "done"))

    def test_list_board_returns_all_cards(self) -> None:
        for cid, col in [("a", "ready"), ("b", "running"), ("c", "done")]:
            self.store.upsert_card({"card_id": cid, "title": cid, "column": col})
        rows = self.store.list_board()
        self.assertEqual({r["card_id"] for r in rows}, {"a", "b", "c"})


class JsonStorePersistenceTests(TempDirMixin, unittest.TestCase):
    """Surviving a process restart is the core restart-persistence contract."""

    def test_data_survives_close_and_reopen(self) -> None:
        path = self.tmp_dir / "inspector.json"
        store1 = JsonStore(path)
        store1.init()
        store1.save_doc({"id": "doc_persist", "task_id": "t_1", "title": "Persist me", "content": "yes", "source": "note"})
        store1.upsert_card({"card_id": "t_persist", "title": "P", "column": "ready"})
        store1.close()

        store2 = JsonStore(path)
        store2.init()
        self.assertEqual(store2.get_doc("doc_persist")["title"], "Persist me")
        self.assertEqual(store2.get_card("t_persist")["column"], "ready")


if __name__ == "__main__":
    unittest.main()