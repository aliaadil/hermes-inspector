# ADR-0002: Plugin scaffold shape

Status: accepted
Date: 2026-07-10

## Context

The Inspector is the first Hermes plugin. We need to commit to a shape now so
every subsequent plugin (kanban-bridge, plane-bridge, cron-tools) follows the
same contract.

## Decisions

### 1. `plugin.yaml` manifest, parsed by a 100-line in-house loader

The manifest declares entry point, events, HTTP routes, config defaults. We
chose YAML because Hermes uses YAML for its own config (`config.yaml` under
`~/.hermes/`). Adding a `js-yaml` dependency for 30 lines of YAML we own is
overkill, so `src/manifest.js` parses our manifest directly. If the format
ever grows beyond what that parser handles (block mappings with nested
mappings, anchors, multi-line scalars), swap to `js-yaml` and delete
`src/manifest.js` — the public surface (`load(path)`) is the same.

### 2. Zero-dep HTTP router

Five endpoints don't justify pulling in express/fastify. `src/router.js` is a
pattern-match dispatcher with a `handle(method, url, req, res)` contract the
host calls. Static files are served from `public/` via the same router, with
traversal protection (`path.normalize` + startsWith check).

### 3. Lifecycle hooks: `onLoad`, `onEvent`, `onUnload`

Three hooks are enough for every plugin shape we've discussed:

- `onLoad({config, manifest}) -> { router, manifest, httpPort? }` runs once at
  startup. The host inspects the returned `router` and mounts it.
- `onEvent(event, payload) -> void` runs for every subscribed event. Errors
  are logged but never thrown — a noisy host must not crash a plugin.
- `onUnload()` runs at shutdown. Idempotent.

### 4. Same module is the standalone runner

`runStandalone({port, data_path, backend})` boots an `http.Server` and mounts
the same router the host would mount. This means the integration test
exercises the exact code path the host uses — no test-only stubs.

### 5. Storage backend selectable at load time

`config.backend = 'sqlite' | 'json'`. Defaults to sqlite. The plugin picks
`src/store.js` or `src/store-json.js` and the rest of the code is
backend-agnostic. Both backends pass the same smoke + integration suites.

### 6. Events → store writes are best-effort

`onEvent` upserts cards / saves docs. If the store throws, we log and move
on. The inspector is a passive observer — it must never block the host's
event loop. (Future: an in-memory queue + retry could be added if backpressure
becomes a concern, but right now SQLite writes are microseconds.)

### 7. Dashboard is plain HTML + vanilla JS

`public/index.html`, `public/styles.css`, `public/app.js`. No build step, no
React, no bundler. The plugin owner can read every line in two minutes and
tweak in the browser. Five endpoints, polled every 5s — fine for an
operator tool, not fine for a multi-user production dashboard.

## Consequences

- Plugin ships with one runtime dep (better-sqlite3) and zero build deps.
- Test suite runs in ~1s, no compilation. `npm test` covers 52 assertions.
- Adding a new HTTP endpoint is one entry in `plugin.yaml`'s `routes` plus a
  handler in `src/router.js`.
- The plugin contract is small enough to embed in the host's plugin loader
  without a registry abstraction — `require(manifest.entry)` and call the
  three hooks.

## Alternatives considered

- **Express + js-yaml**: ~5 deps for the same surface. Rejected.
- **Embed a WebSocket**: not needed; polling works, and SSE/WebSocket
  complicate the host's plugin loader. Could be added later.
- **Plugin-as-Docker**: way too heavy. We're a Node module.