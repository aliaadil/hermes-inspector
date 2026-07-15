"""Tests for the SQLite storage backend.

The SQLite backend is the primary persistence layer for the inspector.
The schema must match the JS store at ``plugins/hermes-inspector/src/schema.sql``
so a future migration tool can read both formats.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parents[1]))

from tests.helpers import TempDirMixin  # noqa: E402

from hermes_inspector.store_sqlite import SqliteStore  # noqa: E402


class SqliteStoreInitTests(TempDirMixin, unittest.TestCase):
    def test_init_creates_file_and_runs_schema(self) -> None:
        path = self.tmp_dir / "inspector.db"
        store = SqliteStore(path)
        store.init()
        self.assertTrue(path.exists())
        store.close()

    def test_init_is_idempotent(self) -> None:
        path = self.tmp_dir / "inspector.db"
        store = SqliteStore(path)
        store.init()
        store.init()  # second call must not raise
        store.close()

    def test_init_creates_parent_directory(self) -> None:
        path = self.tmp_dir / "nested" / "subdir" / "inspector.db"
        SqliteStore(path).init()
        self.assertTrue(path.exists())


class SqliteStoreDocTests(TempDirMixin, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = SqliteStore(self.tmp_dir / "inspector.db")
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
        self.assertIn("id", result)
        self.assertTrue(result["id"].startswith("doc_"))

    def test_save_doc_uses_explicit_id_and_round_trips(self) -> None:
        self.store.save_doc({
            "id": "doc_round_trip",
            "task_id": "t_001",
            "title": "Brief",
            "content": "hello",
            "source": "brief",
        })
        row = self.store.get_doc("doc_round_trip")
        self.assertIsNotNone(row)
        self.assertEqual(row["title"], "Brief")
        self.assertEqual(row["content"], "hello")
        self.assertEqual(row["source"], "brief")
        self.assertEqual(row["task_id"], "t_001")

    def test_save_doc_upserts_on_conflict(self) -> None:
        self.store.save_doc({
            "id": "doc_x",
            "task_id": "t_001",
            "title": "V1",
            "content": "first",
            "source": "note",
        })
        self.store.save_doc({
            "id": "doc_x",
            "task_id": "t_002",
            "title": "V2",
            "content": "second",
            "source": "note",
        })
        row = self.store.get_doc("doc_x")
        self.assertEqual(row["title"], "V2")
        self.assertEqual(row["content"], "second")
        self.assertEqual(row["task_id"], "t_002")

    def test_get_doc_returns_none_for_unknown(self) -> None:
        self.assertIsNone(self.store.get_doc("doc_does_not_exist"))

    def test_list_docs_returns_recent_first(self) -> None:
        for i in range(3):
            self.store.save_doc({
                "id": f"doc_l_{i}",
                "task_id": "t_001",
                "title": f"d{i}",
                "content": "",
                "source": "note",
            })
        rows = self.store.list_docs()
        ids = [r["id"] for r in rows]
        self.assertEqual(len(rows), 3)
        # Each created_at is monotonically increasing (ISO-8601 sorts lexically)
        self.assertEqual(ids, sorted(ids, reverse=True))

    def test_list_docs_filters_by_task_id(self) -> None:
        self.store.save_doc({"id": "doc_a", "task_id": "t_1", "title": "a", "content": "", "source": "note"})
        self.store.save_doc({"id": "doc_b", "task_id": "t_2", "title": "b", "content": "", "source": "note"})
        rows = self.store.list_docs(task_id="t_1")
        self.assertEqual([r["id"] for r in rows], ["doc_a"])

    def test_list_docs_respects_limit(self) -> None:
        for i in range(5):
            self.store.save_doc({"id": f"doc_lim_{i}", "task_id": "t_1", "title": f"d{i}", "content": "", "source": "note"})
        rows = self.store.list_docs(limit=2)
        self.assertEqual(len(rows), 2)


class SqliteStoreCardTests(TempDirMixin, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = SqliteStore(self.tmp_dir / "inspector.db")
        self.store.init()

    def tearDown(self) -> None:
        self.store.close()
        super().tearDown()

    def test_upsert_card_inserts_new(self) -> None:
        self.store.upsert_card({
            "card_id": "t_card_1",
            "title": "Build X",
            "body": "details",
            "column": "ready",
            "parents": ["t_parent_1"],
            "assignee": "builder",
        })
        row = self.store.get_card("t_card_1")
        self.assertIsNotNone(row)
        self.assertEqual(row["title"], "Build X")
        self.assertEqual(row["column"], "ready")
        self.assertEqual(row["parents"], ["t_parent_1"])
        self.assertEqual(row["assignee"], "builder")

    def test_upsert_card_updates_existing(self) -> None:
        self.store.upsert_card({"card_id": "t_card_2", "title": "old", "column": "ready", "parents": []})
        self.store.upsert_card({"card_id": "t_card_2", "title": "new", "column": "running", "parents": []})
        row = self.store.get_card("t_card_2")
        self.assertEqual(row["title"], "new")
        self.assertEqual(row["column"], "running")

    def test_move_card_changes_column(self) -> None:
        self.store.upsert_card({"card_id": "t_card_3", "title": "x", "column": "ready", "parents": []})
        result = self.store.move_card("t_card_3", "done")
        self.assertEqual(result, {"card_id": "t_card_3", "column": "done"})
        self.assertEqual(self.store.get_card("t_card_3")["column"], "done")

    def test_move_card_returns_none_for_unknown(self) -> None:
        self.assertIsNone(self.store.move_card("t_nope", "done"))

    def test_list_board_returns_all_cards(self) -> None:
        for cid, col in [("a", "ready"), ("b", "running"), ("c", "done")]:
            self.store.upsert_card({"card_id": cid, "title": cid, "column": col, "parents": []})
        rows = self.store.list_board()
        self.assertEqual({r["card_id"] for r in rows}, {"a", "b", "c"})

    def test_parents_default_to_empty_list(self) -> None:
        self.store.upsert_card({"card_id": "t_card_4", "title": "x", "column": "ready"})
        row = self.store.get_card("t_card_4")
        self.assertEqual(row["parents"], [])


if __name__ == "__main__":
    unittest.main()