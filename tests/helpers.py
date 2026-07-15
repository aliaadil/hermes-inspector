"""Common helpers shared by Hermes Inspector tests."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


def repo_root() -> Path:
    """Return the absolute path to the hermes-inspector repo root."""
    here = Path(__file__).resolve()
    # tests/ is at the repo root, so parent of this helpers file = repo root.
    return here.parent.parent


def hermes_inspector_pkg_path() -> str:
    """Return the absolute path of the hermes_inspector package.

    Tests import the package by file path so the layout matches what
    Hermes uses at runtime (``register(ctx)`` is called from
    ``<install_dir>/__init__.py`` after the dashboard scans the repo,
    and the package source lives in ``<install_dir>/hermes_inspector/``).
    """
    return str(repo_root())


# Make ``import hermes_inspector`` work without installing the package.
_PKG_PARENT = str(repo_root())
if _PKG_PARENT not in sys.path:
    sys.path.insert(0, _PKG_PARENT)


class TempDirMixin:
    """Mixin that gives each test an isolated temp data directory."""

    def setUp(self) -> None:  # noqa: D401 - unittest hook
        super().setUp()  # type: ignore[misc]
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)

    def tearDown(self) -> None:  # noqa: D401 - unittest hook
        try:
            self._tmp.cleanup()
        finally:
            super().tearDown()  # type: ignore[misc]