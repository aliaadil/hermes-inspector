"""Tests for the repo-root ``register(ctx)`` entry point.

The repo's ``__init__.py`` exposes a single ``register(ctx)`` function
that the Hermes plugin loader calls at startup. This test pins the
shape of what ``register`` does:

* build a store (sqlite by default, json fallback)
* wire the kanban hook callbacks
* register the ``inspector_emit_doc`` tool

The store layout is what ``hermes plugins install aliaadil/hermes-inspector``
ends up with on disk: ``<HERMES_HOME>/plugins/hermes-inspector/data/inspector.db``.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parents[1]))

# Force the package to import from THIS repo (not any installed copy).
_PKG_NAME = "hermes_inspector"


def _load_root_init(repo_root: Path):
    """Load the repo-root ``__init__.py`` as a uniquely-named module."""
    init_path = repo_root / "__init__.py"
    # Use a synthetic module name so we can import even when the same
    # path shows up under different test conditions.
    mod_name = "_hermes_inspector_root_init"
    spec = importlib.util.spec_from_file_location(mod_name, init_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not build spec for {init_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod, mod_name


class FakePluginContext:
    """Minimal stand-in for ``hermes_cli.plugins.PluginContext``.

    Only implements the methods the plugin actually uses.
    """

    def __init__(self):
        self.tools: list = []
        self.hooks: dict = {}
        self.manifest = MagicMock()
        self.manifest.name = "hermes-inspector"

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)

    def register_hook(self, name, callback):
        self.hooks.setdefault(name, []).append(callback)


class RepoRootRegisterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name) / "data"
        self.data_dir.mkdir()
        self._env_backup = dict(os.environ)
        os.environ["HERMES_INSPECTOR_DATA_DIR"] = str(self.data_dir)
        os.environ["HERMES_INSPECTOR_BACKEND"] = "sqlite"
        # Reload the package so it picks up the new env.
        if _PKG_NAME in sys.modules:
            importlib.reload(sys.modules[_PKG_NAME])

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_backup)
        if _PKG_NAME in sys.modules:
            importlib.reload(sys.modules[_PKG_NAME])
        self._tmp.cleanup()

    def test_register_wires_hooks_and_tool(self) -> None:
        repo_root = THIS.parents[1]
        root_mod, mod_name = _load_root_init(repo_root)
        try:
            ctx = FakePluginContext()
            root_mod.register(ctx)

            # Tool registered.
            tool_names = [t["name"] for t in ctx.tools]
            self.assertIn("inspector_emit_doc", tool_names)

            # All three real Hermes kanban hooks wired.
            for hook_name in (
                "kanban_task_claimed",
                "kanban_task_completed",
                "kanban_task_blocked",
            ):
                self.assertIn(hook_name, ctx.hooks, f"missing hook: {hook_name}")
        finally:
            sys.modules.pop(mod_name, None)

    def test_register_creates_db_file(self) -> None:
        repo_root = THIS.parents[1]
        root_mod, mod_name = _load_root_init(repo_root)
        try:
            ctx = FakePluginContext()
            root_mod.register(ctx)
            db = self.data_dir / "inspector.db"
            self.assertTrue(db.exists(), f"expected db at {db}, got {list(self.data_dir.iterdir())}")
        finally:
            sys.modules.pop(mod_name, None)

    def test_register_supports_json_backend(self) -> None:
        os.environ["HERMES_INSPECTOR_BACKEND"] = "json"
        repo_root = THIS.parents[1]
        root_mod, mod_name = _load_root_init(repo_root)
        try:
            ctx = FakePluginContext()
            root_mod.register(ctx)
            json_file = self.data_dir / "inspector.json"
            self.assertTrue(json_file.exists(), f"expected json at {json_file}")
        finally:
            sys.modules.pop(mod_name, None)

    def test_register_rejects_unknown_backend(self) -> None:
        os.environ["HERMES_INSPECTOR_BACKEND"] = "mongodb"
        repo_root = THIS.parents[1]
        root_mod, mod_name = _load_root_init(repo_root)
        try:
            ctx = FakePluginContext()
            with self.assertRaises(ValueError):
                root_mod.register(ctx)
        finally:
            sys.modules.pop(mod_name, None)


if __name__ == "__main__":
    unittest.main()