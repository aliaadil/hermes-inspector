"""Hermes Inspector dashboard plugin — backend API shim.

The Hermes dashboard's web server discovers this file via
``dashboard/manifest.json`` (``api: plugin_api.py``) and mounts the
``router`` attribute under ``/api/plugins/hermes-inspector/``.

The dashboard runs in a SEPARATE process from the agent loop, so
``register(ctx)`` may not have fired yet when the routes mount. The
router is therefore self-contained: handlers lazily construct their
own store on first access. Both paths agree on the on-disk file, so
writes from the agent process (via hooks + the inspector_emit_doc
tool) and reads from the dashboard process see the same data.

The mounted URL surface intentionally mirrors the JS plugin at
``plugins/hermes-inspector/`` so the dashboard UI works unchanged.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

# Make sure the repo's inner package is importable from the dashboard
# process (which imports this file by absolute path). The dashboard
# extension lives at ``<install>/dashboard/plugin_api.py``; the inner
# package sits at ``<install>/hermes_inspector/``.
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# The inner package imports fastapi + pydantic at module load. If those
# are missing in the dashboard process we degrade to a router stub that
# logs the missing dependency; ``_mount_plugin_api_routes`` in the
# dashboard will still try to import us and skip on failure.
try:
    from hermes_inspector.api import build_router  # noqa: E402
    from hermes_inspector import store as _store_module  # noqa: E402
    from hermes_inspector.hooks import VALID_COLUMNS  # noqa: E402

    # Pre-build a router wired against a lazy-initialized store so the
    # dashboard process gets the same SQLite file the agent process
    # wrote into. ``build_router()`` returns the bare APIRouter; the
    # store factory below supplies the per-process singleton on demand.
    from fastapi import APIRouter

    def _build_dashboard_router() -> APIRouter:
        """Mount the inspector API with a lazily-initialized store."""
        router = build_router()
        # Inject a lazy store so handler lookups do not require the
        # agent-side ``register(ctx)`` to have run yet.
        from hermes_inspector.store import make_store, get_store, set_store

        original_get_store = _store_module.get_store

        def lazy_get_store():
            try:
                return original_get_store()
            except RuntimeError:
                # Initialize a fresh store pointing at the same on-disk
                # file the agent used. The data dir comes from the env
                # var set by ``__init__.py`` at register() time, falling
                # back to a sane default under the install dir.
                import os
                data_dir_env = os.environ.get("HERMES_INSPECTOR_DATA_DIR")
                if data_dir_env:
                    data_dir = Path(data_dir_env)
                else:
                    data_dir = _REPO_ROOT / "data"
                backend = (os.environ.get("HERMES_INSPECTOR_BACKEND") or "sqlite").lower()
                db_name = os.environ.get("HERMES_INSPECTOR_DB_NAME") or (
                    "inspector.json" if backend == "json" else "inspector.db"
                )
                data_dir.mkdir(parents=True, exist_ok=True)
                store = make_store(data_dir / db_name, backend=backend)
                store.init()
                set_store(store)
                return store

        _store_module.get_store = lazy_get_store  # type: ignore[assignment]
        # ``api`` imports the symbol at module load — patch the
        # function reference it captured too.
        import hermes_inspector.api as _api_mod
        _api_mod.get_store = lazy_get_store  # type: ignore[assignment]
        return router

    router = _build_dashboard_router()

except Exception as _exc:  # pragma: no cover - dashboard-only fallback
    # Defer the failure to mount time so the dashboard plugin scanner
    # can log it cleanly instead of crashing on import.
    import logging

    log = logging.getLogger(__name__)
    log.warning("hermes-inspector dashboard plugin could not load: %s", _exc)

    router = None


__all__ = ["router"]