# Hermes Inspector

A Hermes plugin that persists every doc Hermes emits (PR summaries, briefs, ADRs, notes) plus a snapshot of kanban board state, and exposes a small web dashboard for browsing both. Storage is `better-sqlite3` by default with a JSON-file fallback for environments where the native binding can't build.

![dashboard placeholder](docs/dashboard.png)

## Install

The plugin ships inside this repo at `plugins/hermes-inspector/`. To install it for a host deployment:

1. Install dependencies and build the native binding:
   ```
   cd plugins/hermes-inspector
   npm install
   ```
2. Make the host load it. The host scans `plugins/*/plugin.yaml`, reads `entry` (`index.js`), and wires up the `events` list. Drop the whole `hermes-inspector/` directory wherever the host expects to find plugins and restart it.
3. Smoke-test before trusting it:
   ```
   npm test          # smoke + integration, both storage backends, plus UI checks
   ```

The plugin node module name is `hermes-inspector` (see `package.json#name`) and it is `require()`d by the host's plugin loader.

## Configure

Config is read from `plugin.yaml#config` and passed to `onLoad({ config })`. All keys are optional; defaults below are what the plugin assumes if a key is omitted.

| Key          | Default                         | Purpose                                                                 |
|--------------|---------------------------------|-------------------------------------------------------------------------|
| `data_path`  | `./data/inspector.db`           | Where the SQLite (or JSON) file lives. Resolved relative to the plugin dir if not absolute. |
| `backend`    | `sqlite`                        | `sqlite` (better-sqlite3) or `json` (zero-dep fallback for hardened envs). |
| `http_port`  | `0` (let host assign)           | Used by the standalone runner only. The host assigns the real port.    |

Refresh interval for the dashboard is fixed at **5 seconds** in `public/app.js` (`POLL_MS = 5000`); the dashboard pauses polling when the tab is hidden. There is no server-side knob — change the constant in `app.js` if you need a different cadence.

The HTTP route prefix is fixed by the manifest at `/plugins/hermes-inspector` (see `plugin.yaml#http.base_path`). To change it, edit the manifest and the corresponding link in any docs or bookmark.

## What gets captured

Lifecycle events the plugin subscribes to (declared in `plugin.yaml#events`):

- `task_created`  → upsert card into column `ready`
- `task_started`  → upsert card into column `running`
- `task_completed`→ upsert card into column `done`
- `task_failed`   → upsert card into column `blocked`
- `doc_emitted`   → save the doc (id, task_id, title, content, source)

Card columns accepted on write and on the `POST /api/board/move` endpoint:

`todo | ready | running | blocked | review | done`

Where it lives on disk:

- SQLite backend → `<data_path>` plus `-wal` and `-shm` sidecar files when WAL is active
- JSON backend   → a single `<data_path>` file (path is reused; extension is informational)

## Open the dashboard

Once the host is running, the dashboard is at:

```
http://<host>:<port>/plugins/hermes-inspector/
```

For a standalone run (used by the tests, not production):

```
node -e "require('./plugins/hermes-inspector').runStandalone({ port: 8080 }).then(r => console.log('listening on', r.port))"
```

The dashboard polls `/api/board` and `/api/docs` every 5 seconds, has text/date/task filters, and lets you drag a card between columns (which `POST`s to `/api/board/move`).

## Reset / wipe state

Stop the host, then delete the data file. The plugin recreates an empty store on next start.

```
# SQLite (delete the db + WAL sidecars; recommend a checkpoint first)
sqlite3 plugins/hermes-inspector/data/inspector.db ".backup plugins/hermes-inspector/data/inspector.db.bak"
rm plugins/hermes-inspector/data/inspector.db plugins/hermes-inspector/data/inspector.db-wal plugins/hermes-inspector/data/inspector.db-shm
```

If you don't have the `sqlite3` CLI, stopping writes briefly and then deleting the three files is safe — `better-sqlite3` reopens cleanly.

## Privacy

All data is local. The plugin makes no external HTTP calls, no telemetry, no auth. The dashboard listens on whatever port the host exposes; if your host binds to `0.0.0.0`, anyone with network access can read it. Bind it to `127.0.0.1` or put it behind auth if that matters.

## HTTP API

All routes are mounted under `/plugins/hermes-inspector` (the manifest's `http.base_path`).

| Method | Path               | Description                                                            |
|--------|--------------------|------------------------------------------------------------------------|
| GET    | `/health`          | Liveness probe. JSON `{ status, uptime_seconds }`.                     |
| GET    | `/api/docs`        | List docs. Query: `?task_id`, `?since` (ISO-8601 or epoch-ms), `?limit`. |
| GET    | `/api/docs/:id`    | One doc, full content.                                                 |
| GET    | `/api/board`       | All cards grouped by column.                                           |
| POST   | `/api/board/move`  | Move a card. Body: `{ "card_id": "...", "to_column": "running" }`.     |

Static assets (`/`, `/styles.css`, `/app.js`) are served from `public/`.

## Layout

```
plugins/hermes-inspector/
├── plugin.yaml          # Hermes plugin manifest (entry, events, http routes, config)
├── index.js             # plugin entry point — onLoad/onEvent/onUnload + standalone runner
├── package.json         # main: index.js; npm test = smoke + integration + UI
├── src/
│   ├── schema.sql       # SQLite schema (docs + kanban)
│   ├── store.js         # better-sqlite3 backend
│   ├── store-json.js    # JSON-file fallback (same API surface as store.js)
│   ├── router.js        # tiny pattern-match HTTP router (no framework)
│   └── manifest.js      # tiny YAML loader for plugin.yaml (zero-dep)
├── public/              # static dashboard assets served at /
│   ├── index.html
│   ├── styles.css
│   └── app.js           # vanilla JS, no build step; POLL_MS = 5000
├── test/
│   ├── smoke.js         # storage layer — write/read docs + cards, both backends
│   ├── integration.js   # full plugin lifecycle — events → HTTP → restart-survives
│   └── ui.js            # static-analysis checks on index.html + app.js
├── docs/
│   ├── adr-persistence.md
│   ├── adr-dashboard-ui.md
│   └── dashboard.png    # (placeholder)
└── data/                # gitignored: inspector.db (+ wal/shm)
```

## Backup

```
sqlite3 plugins/hermes-inspector/data/inspector.db ".backup inspector.db.bak"
```

Or stop writes, then copy the `inspector.db` plus `-wal` and `-shm` files together — the three are a single consistent snapshot.

## License

MIT. See top-level `LICENSE` if present, otherwise the `license` field in `plugin.yaml`.
