"""Tests for the ``inspector_emit_doc`` tool registration.

The tool is the canonical way for an agent to push an emitted doc into
the inspector's store. The hook handlers + the API router are both
covered by their own test modules; this test pins the tool surface so
changes to the schema are loud failures.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parents[1]))

from tests.helpers import TempDirMixin  # noqa: E402

from hermes_inspector import tool  # noqa: E402
from hermes_inspector.store import make_store, set_store  # noqa: E402


class ToolSchemaTests(unittest.TestCase):
    def test_tool_spec_has_required_fields(self) -> None:
        spec = tool.SPEC
        self.assertEqual(spec["name"], "inspector_emit_doc")
        self.assertIn("description", spec)
        self.assertIn("input_schema", spec)
        self.assertEqual(spec["input_schema"]["type"], "object")
        # task_id is mandatory; everything else is optional.
        props = spec["input_schema"]["properties"]
        self.assertIn("task_id", props)
        self.assertIn("title", props)
        self.assertIn("content", props)
        self.assertIn("source", props)
        self.assertIn("id", props)

    def test_handler_writes_doc_row(self) -> None:
        captured: dict = {}

        class _FakeStore:
            def save_doc(self, data):
                captured.update(data)
                return {"id": "doc_from_tool"}

        set_store(_FakeStore())  # type: ignore[arg-type]
        try:
            result = tool.handler(
                task_id="t_99",
                title="From the tool",
                content="body",
                source="brief",
                id="doc_explicit",
            )
        finally:
            set_store(None)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["doc_id"], "doc_from_tool")
        self.assertEqual(captured["task_id"], "t_99")
        self.assertEqual(captured["id"], "doc_explicit")
        self.assertEqual(captured["source"], "brief")

    def test_handler_requires_task_id(self) -> None:
        captured: list = []

        class _FakeStore:
            def save_doc(self, data):
                captured.append(data)
                return {"id": "doc_x"}

        set_store(_FakeStore())  # type: ignore[arg-type]
        try:
            result = tool.handler(title="orphan", content="", source="note")
        finally:
            set_store(None)
        self.assertEqual(result["status"], "error")
        self.assertIn("task_id", result["error"])
        self.assertEqual(captured, [])

    def test_handler_returns_error_string_when_store_missing(self) -> None:
        set_store(None)
        result = tool.handler(task_id="t_1", title="x")
        self.assertEqual(result["status"], "error")
        self.assertIn("store", result["error"])


class ToolRegistrationTests(unittest.TestCase):
    """Smoke-test the register(ctx) shape so refactors don't break the contract."""

    def test_register_installs_tool_on_fake_ctx(self) -> None:
        ctx = MagicMock()
        tool.register(ctx)
        ctx.register_tool.assert_called_once()
        kwargs = ctx.register_tool.call_args.kwargs
        self.assertEqual(kwargs["name"], "inspector_emit_doc")
        self.assertEqual(kwargs["toolset"], "inspector")
        self.assertIn("schema", kwargs)
        self.assertEqual(kwargs["handler"], tool.handler)


if __name__ == "__main__":
    unittest.main()