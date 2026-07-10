// Hermes Inspector — plugin entry point.
//
// Loaded by the Hermes plugin host via `require('@hermes/plugin-inspector')`
// (resolved to this package by the host's plugin loader). Exports the lifecycle
// hooks the host calls during startup, event dispatch, and shutdown.
//
// The host contract is intentionally small so the same file can also be
// invoked standalone (`node index.js`) for smoke testing — see
// `bin/run-standalone.js`.
//
// Persistence: backed by `src/store.js` (better-sqlite3) by default, with the
// JSON fallback in `src/store-json.js` available when the native binding
// can't be built. The choice is made once in `onLoad` from plugin config and
// is logged so operators can see which backend came up.

'use strict';

const fs = require('fs');
const path = require('path');
const http = require('http');

const manifest = require('./src/manifest').load(path.join(__dirname, 'plugin.yaml'));
const storeSqlite = require('./src/store');
const storeJson = require('./src/store-json');
const { createRouter } = require('./src/router');

// ---------------------------------------------------------------------------
// Module state. A single plugin instance owns one store handle and one router.
// The host is expected to call onLoad exactly once before onEvent/http calls.
// ---------------------------------------------------------------------------

let store = null;          // active store backend (sqlite or json)
let storeKind = null;      // 'sqlite' | 'json' — for diagnostics
let httpServer = null;     // when running standalone
let httpPort = null;       // 0 when not listening
const logLines = [];       // short rolling log surfaced on /health

function log(...args) {
  const line = `[${new Date().toISOString()}] ${args.map(String).join(' ')}`;
  logLines.push(line);
  if (logLines.length > 200) logLines.shift();
  // eslint-disable-next-line no-console
  console.log(line);
}

function pickStore(config) {
  if ((config.backend || 'sqlite') === 'json') return { store: storeJson, kind: 'json' };
  return { store: storeSqlite, kind: 'sqlite' };
}

// ---------------------------------------------------------------------------
// Lifecycle hooks
// ---------------------------------------------------------------------------

// onLoad({ config, manifest }) -> Promise<{ router, httpPrefix?, manifest }>
// The host calls this once after loading the plugin. We initialize the store
// and return a router the host mounts under `http.base_path`.
async function onLoad({ config = {} } = {}) {
  const chosen = pickStore(config);
  store = chosen.store;
  storeKind = chosen.kind;

  const dataPath = config.data_path
    || path.join(__dirname, 'data', 'inspector.db');
  await store.init({ path: dataPath });

  log(`hermes-inspector ready (backend=${storeKind}, data=${dataPath})`);

  const router = createRouter({ store, manifest });
  const result = { router, manifest };
  if (config.http_port !== undefined) result.httpPort = Number(config.http_port);
  return result;
}

// onEvent(eventName, payload) -> Promise<void>
// Called by the host for every subscribed event. We translate events into
// store writes so the inspector's view stays current with what Hermes is
// doing. Unknown events are logged and ignored — never throw, so a noisy
// host can't bring the plugin down.
async function onEvent(event, payload = {}) {
  if (!store) {
    log(`onEvent called before onLoad (event=${event}) — dropping`);
    return;
  }
  try {
    switch (event) {
      case 'task_created':
      case 'task_started':
      case 'task_failed':
        // Cards transition into ready/running/blocked. We upsert so first
        // sighting of the card creates the row.
        await store.upsertCard({
          card_id: payload.id || payload.task_id,
          title: payload.title || '(untitled)',
          body: payload.body || '',
          column: columnForEvent(event),
          parents: Array.isArray(payload.parents) ? payload.parents : [],
          assignee: payload.assignee || null,
        });
        break;

      case 'task_completed':
        await store.upsertCard({
          card_id: payload.id || payload.task_id,
          title: payload.title || '(untitled)',
          body: payload.body || '',
          column: 'done',
          parents: Array.isArray(payload.parents) ? payload.parents : [],
          assignee: payload.assignee || null,
        });
        break;

      case 'doc_emitted':
        await store.saveDoc({
          id: payload.id,
          task_id: payload.task_id || 'unscoped',
          title: payload.title || '(untitled doc)',
          content: payload.content || '',
          source: payload.source || 'note',
        });
        break;

      default:
        log(`onEvent: ignored unknown event '${event}'`);
    }
  } catch (err) {
    // Never let a bad event crash the plugin; surface and continue.
    log(`onEvent error (${event}): ${err.message}`);
  }
}

// onUnload() -> Promise<void>
// Closes the store and (if standalone) the HTTP server. Idempotent.
async function onUnload() {
  if (httpServer) {
    await new Promise((resolve) => httpServer.close(resolve));
    httpServer = null;
    httpPort = null;
  }
  if (store) {
    await store.close();
    store = null;
  }
  log('hermes-inspector unloaded');
}

function columnForEvent(event) {
  switch (event) {
    case 'task_created': return 'ready';
    case 'task_started': return 'running';
    case 'task_failed':  return 'blocked';
    default:             return 'ready';
  }
}

// Convenience for the dashboard's "since" filter on /api/docs.
function parseSince(raw) {
  if (!raw) return null;
  const n = Number(raw);
  if (!Number.isNaN(n) && n > 0) {
    // Treat bare numbers as epoch-ms.
    return new Date(n).toISOString();
  }
  const d = new Date(raw);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

// ---------------------------------------------------------------------------
// Standalone runner — enables `node plugins/hermes-inspector/bin/run-standalone.js`
// for testing without the host. The host does NOT call this; it only invokes
// the lifecycle hooks above.
// ---------------------------------------------------------------------------

async function runStandalone({ port = 0, data_path, backend } = {}) {
  await onLoad({ config: { http_port: port, data_path, backend } });
  const router = createRouter({ store, manifest });

  httpServer = http.createServer(async (req, res) => {
    try {
      // Mount the standalone router under the plugin's declared base_path so
      // the URLs the host would expose are identical to what we serve here.
      const prefix = manifest.http.base_path;
      const url = req.url.startsWith(prefix) ? req.url.slice(prefix.length) || '/' : req.url;
      const handled = await router.handle(req.method, url, req, res);
      if (!handled) {
        res.statusCode = 404;
        res.setHeader('content-type', 'application/json');
        res.end(JSON.stringify({ error: 'not_found', path: req.url }));
      }
    } catch (err) {
      res.statusCode = 500;
      res.setHeader('content-type', 'application/json');
      res.end(JSON.stringify({ error: 'internal', message: err.message }));
    }
  });

  await new Promise((resolve) => httpServer.listen(port, resolve));
  httpPort = httpServer.address().port;
  log(`standalone http listening on http://127.0.0.1:${httpPort}${manifest.http.base_path}`);
  return { port: httpPort, close: onUnload };
}

module.exports = {
  manifest,
  onLoad,
  onEvent,
  onUnload,
  // Exposed for tests + standalone runner:
  runStandalone,
  parseSince,
  _log: () => [...logLines],
};