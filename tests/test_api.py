"""Tests for the FastAPI dashboard router.

The dashboard auto-mounts this router under ``/api/plugins/hermes-inspector/``
after discovering ``dashboard/manifest.json``. The router exposes the same
endpoints the JS implementation did, so the dashboard UI keeps working
without any frontend changes.

Tests use FastAPI's ``TestClient`` so we exercise the real
request/response cycle (pydantic validation, status codes, JSON
serialization) rather than poking private handler functions.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parents[1]))

from tests.helpers import TempDirMixin  # noqa: E402

from hermes_inspector import api  # noqa: E402
from hermes_inspector.store import make_store, set_store  # noqa: E402

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    _HAS_FASTAPI = True
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False


class ApiHelperTests(unittest.TestCase):
    def test_parse_since_iso_returns_iso(self) -> None:
        self.assertEqual(
            api.parse_since("2026-01-01T00:00:00Z"),
            "2026-01-01T00:00:00Z",
        )

    def test_parse_since_epoch_ms_returns_iso(self) -> None:
        result = api.parse_since("0")
        self.assertEqual(result, "1970-01-01T00:00:00Z")

    def test_parse_since_bad_input_returns_none(self) -> None:
        self.assertIsNone(api.parse_since("not a date"))

    def test_parse_since_none_returns_none(self) -> None:
        self.assertIsNone(api.parse_since(None))

    def test_parse_since_empty_returns_none(self) -> None:
        self.assertIsNone(api.parse_since(""))


@unittest.skipUnless(_HAS_FASTAPI, "fastapi not installed in this environment")
class ApiRouterTests(TempDirMixin, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = make_store(self.tmp_dir / "inspector.db", backend="sqlite")
        self.store.init()
        set_store(self.store)
        app = FastAPI()
        app.include_router(api.build_router())
        self.client = TestClient(app)

    def tearDown(self) -> None:
        set_store(None)
        self.store.close()
        super().tearDown()

    def test_health_returns_ok(self) -> None:
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["plugin"], "hermes-inspector")

    def test_list_docs_empty(self) -> None:
        r = self.client.get("/api/docs")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["count"], 0)
        self.assertEqual(body["docs"], [])

    def test_save_then_list_doc(self) -> None:
        r = self.client.post("/api/docs", json={
            "task_id": "t_1",
            "title": "Brief",
            "content": "hello",
            "source": "brief",
        })
        self.assertEqual(r.status_code, 200)
        self.assertIn("doc_id", r.json())

        listing = self.client.get("/api/docs")
        self.assertEqual(listing.status_code, 200)
        body = listing.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["docs"][0]["title"], "Brief")

    def test_get_doc_by_id(self) -> None:
        self.client.post("/api/docs", json={
            "id": "doc_target",
            "task_id": "t_1",
            "title": "Get me",
            "content": "body",
            "source": "note",
        })
        r = self.client.get("/api/docs/doc_target")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["title"], "Get me")
        self.assertEqual(body["content"], "body")

    def test_get_doc_404_for_unknown(self) -> None:
        r = self.client.get("/api/docs/nope")
        self.assertEqual(r.status_code, 404)

    def test_list_docs_filters_by_task_id(self) -> None:
        self.client.post("/api/docs", json={"task_id": "t_1", "title": "a", "content": "", "source": "note"})
        self.client.post("/api/docs", json={"task_id": "t_2", "title": "b", "content": "", "source": "note"})
        r = self.client.get("/api/docs", params={"task_id": "t_1"})
        self.assertEqual(r.json()["count"], 1)
        self.assertEqual(r.json()["docs"][0]["title"], "a")

    def test_list_board_empty(self) -> None:
        r = self.client.get("/api/board")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["count"], 0)

    def test_move_card(self) -> None:
        self.store.upsert_card({"card_id": "t_mv", "title": "Move me", "column": "ready"})
        r = self.client.post("/api/board/move", json={"card_id": "t_mv", "to_column": "done"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["column"], "done")
        self.assertEqual(self.store.get_card("t_mv")["column"], "done")

    def test_move_card_rejects_unknown_column(self) -> None:
        self.store.upsert_card({"card_id": "t_mv2", "title": "x", "column": "ready"})
        r = self.client.post("/api/board/move", json={"card_id": "t_mv2", "to_column": "bogus"})
        self.assertEqual(r.status_code, 422)

    def test_move_card_404_for_unknown(self) -> None:
        r = self.client.post("/api/board/move", json={"card_id": "t_nope", "to_column": "done"})
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()