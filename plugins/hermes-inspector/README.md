# Hermes Inspector (legacy JS plugin)

> **DEPRECATED — read the root README instead.**
> The currently-installed plugin lives at the **repository root** of
> `aliaadil/hermes-inspector`. `hermes plugins install` clones the
> whole repo into `~/.hermes/plugins/hermes-inspector/` and loads the
> Python `register(ctx)` entry point at the root. This directory
> (`plugins/hermes-inspector/`) is a standalone Node.js plugin kept
> for backwards compatibility with the original prototype and is
> **not** what Hermes v0.18.x loads on `hermes plugins install`.
>
> **For install, configuration, hooks, and dashboard contract, see
> the README at the repository root.**
>
> The lifecycle event names listed below (`task_created`,
> `task_started`, `task_completed`, `task_failed`, `doc_emitted`) and
> the `entry: index.js` contract described below are **not** what
> Hermes v0.18.x fires or accepts. The current contract is:
>
> * hooks: `kanban_task_claimed`, `kanban_task_completed`, `kanban_task_blocked`
> * tool: `inspector_emit_doc` (registered via `ctx.register_tool`)
> * entry: `__init__.py::register(ctx)` at the repo root
>
> The legacy plugin still works as a standalone Node app on its own
> port (run `node index.js` from this directory) and its test suite
> (`npm test`, 82 assertions) still passes against its own contract.

## What this directory contains

This is the **standalone JavaScript prototype** of Hermes Inspector.
It is preserved in-tree so the dashboard UI, storage layer, and
test suite can be exercised without going through the full Hermes
plugin loader. None of the host-facing integration described below
matches current Hermes plugin discovery.

Storage is better-sqlite3 by default; a JSON-file fallback ships for
environments where the native binding can't be built.

## Original contract (legacy)

> The rest of this file describes the **legacy** JS contract. It is
> kept as historical reference and to support the local `npm test`
> suite. Do not wire a new Hermes install against it.

A Hermes plugin that:

1. **Persists** every doc Hermes emits (PR summaries, briefs, ADRs, notes) and a snapshot of kanban board state.
2. **Subscribes** to lifecycle events (`task_created`, `task_started`, `task_completed`, `task_failed`, `doc_emitted`) and writes them to the store.
3. **Exposes** an HTTP dashboard under `/plugins/hermes-inspector` for browsing docs, inspecting cards, and moving cards between columns.

## Layout

```
plugins/hermes-inspector/
├── plugin.yaml          # Hermes plugin manifest (entry, events, http routes, config)
├── index.js             # plugin entry point — onLoad/onEvent/onUnload + standalone runner
├── package.json         # main: index.js; npm test runs smoke + integration
├── src/
│   ├── schema.sql       # SQLite schema (docs + kanban)
│   ├── store.js         # better-sqlite3 backend
│   ├── store-json.js    # JSON-file fallback (same API)
│   ├── router.js        # tiny pattern-match HTTP router (no framework)
│   └── manifest.js      # tiny YAML loader for plugin.yaml (zero-dep)
├── public/              # static dashboard assets served at /
│   ├── index.html
│   ├── styles.css
│   └── app.js
├── test/
│   ├── smoke.js         # storage layer — write/read docs + cards, both backends
│   └── integration.js   # full plugin lifecycle — events → HTTP → restart-survives
├── docs/adr-persistence.md
└── data/                # gitignored: inspector.db (+ wal/shm)
```

## Plugin contract

Hermes loads `index.js` and calls the exported lifecycle hooks:

```js
const plugin = require('@hermes/plugin-inspector');

const { router } = await plugin.onLoad({ config: { data_path: './data/inspector.db' } });
// Mount `router` at /plugins/hermes-inspector on the host's HTTP server.

await plugin.onEvent('task_created', { id: 't_42', title: 'Ship X' });
await plugin.onEvent('doc_emitted',  { task_id: 't_42', title: 'Brief', content: '...', source: 'brief' });

// On shutdown:
await plugin.onUnload();
```

The router exposes a `handle(method, url, req, res) -> Promise<boolean>` so the host can call it inside its own request handler. Static assets are served from `public/` at `/`.

## HTTP API

All routes are mounted under the plugin's `http.base_path` (`/plugins/hermes-inspector` by default). The standalone runner used by tests mounts at `/plugins/hermes-inspector/...` — strip that prefix when calling the standalone server.

| Method | Path                  | Description                                                 |
|--------|-----------------------|-------------------------------------------------------------|
| GET    | `/health`             | Liveness probe.                                             |
| GET    | `/api/docs`           | List docs. Query: `?task_id`, `?since` (ISO-8601 or ms), `?limit`. |
| GET    | `/api/docs/:id`       | Fetch one doc.                                              |
| GET    | `/api/board`          | List all cards grouped by column.                           |
| POST   | `/api/board/move`     | Move a card. Body: `{ "card_id": "...", "to_column": "done" }`. |

`to_column` must be one of `todo | ready | running | blocked | review | done`.

## Quick start

```
node plugins/hermes-inspector/index.js        # boots standalone on a random port
npm --prefix plugins/hermes-inspector test    # runs smoke + integration
```

Programmatic:

```js
const inspector = require('./plugins/hermes-inspector');
const { router, manifest } = await inspector.onLoad({ config: { data_path: './data/inspector.db' } });
// attach `router` to your HTTP server, then call inspector.onEvent(...) for each lifecycle event.
```

## Tests

```
npm test               # smoke + integration, both backends (52 assertions)
npm run smoke          # storage layer only
npm run test:integration
```

The integration test boots the plugin twice against the same data file and asserts that cards + docs survive a restart — matching the acceptance criteria in the task spec.

## Plugin manifest (`plugin.yaml`)

```yaml
name: hermes-inspector
version: 0.2.0
entry: index.js
events:
  - task_created
  - task_started
  - task_completed
  - task_failed
  - doc_emitted
http:
  base_path: /plugins/hermes-inspector
  static_dir: public
  routes: [...]
config:
  data_path: ./data/inspector.db
  backend: sqlite          # or "json"
```

The host reads `entry` to know what to `require`, scans `events` to validate subscriptions, and mounts the HTTP routes under `http.base_path`.

## Why a hand-rolled YAML loader?

`plugin.yaml` is small and written by us. Adding `js-yaml` for ~30 lines of YAML we control is overkill, so the manifest is parsed by `src/manifest.js`. Swap to `js-yaml` if the manifest grows beyond what that parser handles (anything beyond plain keys, scalar values, and inline/block lists).

## Backup

```
cp data/inspector.db data/inspector.db.bak
```

WAL mode means you may also want `inspector.db-wal` for an exact point-in-time copy. Either `sqlite3 inspector.db .backup inspector.db.bak` or stop writes briefly while copying.