# Hermes Inspector

Persists every doc Hermes emits (PR summaries, briefs, ADRs, notes) plus a
snapshot of kanban board state, and exposes a small HTTP dashboard for
inspection and manual card moves.

This repository is a **Hermes Agent v0.18.x plugin**: `hermes plugins
install` clones it into `~/.hermes/plugins/hermes-inspector/` and loads
its Python `register(ctx)` entry point.

---

## Install

```bash
# From this repo's directory (or use the GitHub shorthand):
hermes plugins install aliaadil/hermes-inspector --enable
```

The `--enable` flag adds `hermes-inspector` to `plugins.enabled` in
`config.yaml` so the plugin loads at next launch. Without it, the
plugin stays installed but dormant until you run
`hermes plugins enable hermes-inspector`.

Verify:

```bash
hermes plugins list --plain --no-bundled
# enabled      git      1.0.0    hermes-inspector
```

---

## What it does

The plugin registers itself with the host via the official Hermes
plugin API:

| Channel | Mechanism | Data captured |
|---|---|---|
| `kanban_task_claimed` | lifecycle hook | a card row (column = `ready`) |
| `kanban_task_completed` | lifecycle hook | card row (column = `done`, summary appended to body) |
| `kanban_task_blocked` | lifecycle hook | card row (column = `blocked`, reason appended to body) |
| `inspector_emit_doc` | tool registered via `ctx.register_tool` | doc row linked to a task id |
| Dashboard `/api/plugins/hermes-inspector/` | FastAPI router | read access + manual `POST /api/board/move` |

Hermes Agent core does not currently emit a `doc_emitted` hook, so docs
are captured by agents calling the `inspector_emit_doc` tool directly.

The dashboard frontend (two-pane kanban + docs view) is shipped under
`dashboard/dist/` and is loaded by the host dashboard automatically once
the plugin is enabled.

---

## Configuration (environment variables)

All settings are read at `register(ctx)` time. None are required.

| Variable | Default | Notes |
|---|---|---|
| `HERMES_INSPECTOR_DATA_DIR` | `./data` relative to the install root | Where the store file lives. The host dashboard must agree on this path — see "Running the host dashboard" below. |
| `HERMES_INSPECTOR_BACKEND` | `sqlite` | `sqlite` (primary) or `json` (fallback). |
| `HERMES_INSPECTOR_DB_NAME` | `inspector.db` / `inspector.json` | Override the filename. |

When the host dashboard runs in a separate process from the agent loop,
export the same `HERMES_INSPECTOR_*` variables so both processes point
at the same on-disk file. The dashboard plugin's
`dashboard/plugin_api.py` resolves them at startup.

---

## Running the host dashboard

```bash
# Agent process (where hooks fire):
hermes

# Dashboard process (where the web UI + API mount):
export HERMES_INSPECTOR_DATA_DIR="$HOME/.hermes/inspector-data"
hermes dashboard --port 8080
```

Then open `http://127.0.0.1:8080` and click the **Inspector** tab.

---

## Verify the plugin contract

A complete acceptance run — install, real-Hermes integration test, and
both test suites — is one command:

```bash
bash scripts/verify_plugin_contract.sh
```

This script:

1. Creates an isolated `HERMES_HOME` (or reuses the one in `$HERMES_HOME`).
2. Runs `hermes plugins install file://$PWD --enable --force`.
3. Runs `tests/integration_test.py`, which discovers the plugin via the
   real `hermes_cli.plugins.PluginManager`, then drives the real
   `kanban_task_claimed`, `kanban_task_completed`, `kanban_task_blocked`
   hooks through the real `hermes_cli.kanban_db` transitions and invokes
   the real `inspector_emit_doc` tool via the host's tool registry.
4. Runs the Python unit suite (`tests/`).
5. Runs the JS plugin suite (`plugins/hermes-inspector/test/`).

Each step exits non-zero on failure so CI can gate on it.

---

## Tests

### Python (62 tests, in this repo)

```bash
/opt/hermes/.venv/bin/python -m unittest discover tests -v
```

Covers: store backends (sqlite + json), hook handlers, tool
registration, the `register(ctx)` entry point, and the dashboard API.

### Python integration (1 test, real Hermes)

```bash
export HERMES_HOME=<isolated> MOCK_AUTH=true
/opt/hermes/.venv/bin/python tests/integration_test.py
```

Drives the plugin through the real `hermes_cli.plugins.PluginManager`
+ `hermes_cli.kanban_db` paths.

### JavaScript (82 tests, in `plugins/hermes-inspector/`)

```bash
cd plugins/hermes-inspector
npm install
npm test
```

Covers: smoke tests for both backends, integration tests for the
HTTP API, and Playwright UI tests for drag-and-drop + persistence.

---

## Repository layout

```
.
├── plugin.yaml                       # Hermes plugin manifest (root)
├── __init__.py                       # register(ctx) entry point
├── hermes_inspector/                 # Python source package
│   ├── __init__.py
│   ├── store.py                      # Store factory + process singleton
│   ├── store_sqlite.py               # Primary backend
│   ├── store_json.py                 # Fallback backend
│   ├── hooks.py                      # kanban hook handlers + emit_doc
│   ├── tool.py                       # inspector_emit_doc tool spec
│   └── api.py                        # FastAPI router for the dashboard
├── dashboard/                        # Dashboard extension (auto-mounted)
│   ├── manifest.json                 # discoverable by the host
│   ├── plugin_api.py                 # FastAPI shim (re-exports api.router)
│   └── dist/                         # Compiled dashboard UI
├── tests/                            # Python unit + integration tests
├── scripts/
│   └── verify_plugin_contract.sh     # Full QA acceptance run
└── plugins/hermes-inspector/         # Legacy JS plugin (preserved)
```

The legacy JS plugin under `plugins/hermes-inspector/` is kept for
backwards compatibility (its standalone dashboard still works on its
own port). It is NOT the install target — `hermes plugins install
aliaadil/hermes-inspector` clones the repository root, not this nested
subdirectory.

---

## License

MIT.