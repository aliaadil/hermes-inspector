// HTTP router for the Hermes Inspector plugin.
//
// Deliberately tiny: pattern match on (method, path) and dispatch. Pulling in
// express/fastify would add a dependency and a version surface for what is
// five endpoints. The contract the host cares about:
//
//   router.handle(method, url, req, res) -> Promise<boolean>
//
// Returns true if the request was handled (status code already set on res),
// false otherwise (caller decides whether to 404 or fall through).
//
// Static assets from `public/` are served via handleStatic, which the host
// calls before the API matcher so a request for /plugins/hermes-inspector/
// returns index.html.

'use strict';

const fs = require('fs');
const fsp = require('fs/promises');
const path = require('path');
const { URL } = require('url');

const STATIC_ROOT = path.join(__dirname, '..', 'public');

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js':   'application/javascript; charset=utf-8',
  '.mjs':  'application/javascript; charset=utf-8',
  '.css':  'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg':  'image/svg+xml',
  '.png':  'image/png',
  '.ico':  'image/x-icon',
  '.txt':  'text/plain; charset=utf-8',
  '.map':  'application/json; charset=utf-8',
};

function send(res, status, body, headers = {}) {
  res.statusCode = status;
  for (const [k, v] of Object.entries(headers)) res.setHeader(k, v);
  if (typeof body === 'string' || Buffer.isBuffer(body)) {
    res.end(body);
  } else {
    res.setHeader('content-type', 'application/json; charset=utf-8');
    res.end(JSON.stringify(body));
  }
}

async function readJsonBody(req, maxBytes = 1024 * 1024) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let total = 0;
    req.on('data', (chunk) => {
      total += chunk.length;
      if (total > maxBytes) {
        reject(new Error('payload_too_large'));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8');
      if (!raw) return resolve({});
      try { resolve(JSON.parse(raw)); } catch (e) { reject(new Error('invalid_json')); }
    });
    req.on('error', reject);
  });
}

// Static-file resolver. Tries `public/<rel>` then `public/<rel>/index.html` so
// `/` resolves to `/index.html`. Path traversal is rejected by resolving the
// requested path and asserting it stays inside STATIC_ROOT.
async function serveStatic(rel, res) {
  if (!rel || rel === '/') rel = '/index.html';
  const target = path.normalize(path.join(STATIC_ROOT, rel));
  if (!target.startsWith(STATIC_ROOT)) {
    return send(res, 403, { error: 'forbidden' });
  }
  let stat;
  try {
    stat = await fsp.stat(target);
  } catch (_) {
    // Try dir/index.html
    try {
      const idx = path.join(target, 'index.html');
      stat = await fsp.stat(idx);
      return serveFile(idx, res);
    } catch (_) {
      return send(res, 404, { error: 'not_found', path: rel });
    }
  }
  if (stat.isDirectory()) return serveFile(path.join(target, 'index.html'), res);
  return serveFile(target, res);
}

async function serveFile(p, res) {
  try {
    const data = await fsp.readFile(p);
    const ext = path.extname(p).toLowerCase();
    res.setHeader('content-type', MIME[ext] || 'application/octet-stream');
    res.setHeader('cache-control', 'no-cache');
    res.statusCode = 200;
    res.end(data);
    return true;
  } catch (err) {
    if (err.code === 'ENOENT') return send(res, 404, { error: 'not_found' });
    return send(res, 500, { error: 'read_error', message: err.message });
  }
}

// ---------------------------------------------------------------------------
// Route handlers. Each takes (store, params, query, req, res).
// ---------------------------------------------------------------------------

async function listDocsHandler(store, { query }, _req, res) {
  const task_id = query.task_id || undefined;
  const since = query.since || undefined;       // ISO-8601 or epoch-ms
  let rows = await store.listDocs({ task_id });
  if (since) {
    const sinceIso = since;
    rows = rows.filter((r) => r.created_at && r.created_at >= sinceIso);
  }
  if (query.limit) rows = rows.slice(0, Number(query.limit) || 100);
  send(res, 200, { count: rows.length, docs: rows });
}

async function getDocHandler(store, { params }, _req, res) {
  const row = await store.getDoc(params.id);
  if (!row) return send(res, 404, { error: 'not_found', id: params.id });
  send(res, 200, row);
}

async function listBoardHandler(store, _ctx, _req, res) {
  const board = await store.listBoard();
  send(res, 200, { count: board.length, cards: board });
}

async function moveCardHandler(store, { body }, _req, res) {
  const { card_id, to_column } = body || {};
  if (!card_id || !to_column) {
    return send(res, 400, { error: 'bad_request', message: 'card_id and to_column are required' });
  }
  const valid = ['todo', 'ready', 'running', 'blocked', 'review', 'done'];
  if (!valid.includes(to_column)) {
    return send(res, 400, { error: 'bad_request', message: `to_column must be one of ${valid.join(', ')}` });
  }
  const result = await store.moveCard(card_id, to_column);
  if (!result) return send(res, 404, { error: 'not_found', card_id });
  send(res, 200, result);
}

async function healthHandler(_store, _ctx, _req, res) {
  // Minimal liveness signal. Includes a small log tail so operators can see
  // the last few lines without SSH'ing in.
  send(res, 200, { status: 'ok', uptime_seconds: Math.round(process.uptime()) });
}

// ---------------------------------------------------------------------------
// Pattern matcher
// ---------------------------------------------------------------------------

// Each pattern is { method, regex, handler, paramNames }. Order matters —
// more-specific patterns go first.
function buildRouteTable() {
  return [
    { method: 'GET',  regex: /^\/api\/docs\/([A-Za-z0-9_\-:.]+)$/, handler: getDocHandler,    params: ['id'] },
    { method: 'GET',  regex: /^\/api\/docs$/,                     handler: listDocsHandler,  params: [] },
    { method: 'POST', regex: /^\/api\/board\/move$/,              handler: moveCardHandler,  params: [] },
    { method: 'GET',  regex: /^\/api\/board$/,                    handler: listBoardHandler, params: [] },
    { method: 'GET',  regex: /^\/health$/,                        handler: healthHandler,    params: [] },
  ];
}

function createRouter({ store, manifest: _manifest }) {
  const routes = buildRouteTable();

  async function handle(method, url, req, res) {
    let parsed;
    try {
      parsed = new URL(url, 'http://placeholder');
    } catch (_) {
      send(res, 400, { error: 'bad_url' });
      return true;
    }
    const pathname = parsed.pathname;
    const query = Object.fromEntries(parsed.searchParams.entries());

    // 1) Try static assets first for non-/api/ paths.
    if (!pathname.startsWith('/api/') && pathname !== '/health') {
      const rel = pathname === '/' ? '/index.html' : pathname.replace(/^\/+/, '');
      const handled = await serveStatic(rel, res);
      return handled !== false;
    }

    // 2) Match API/health routes.
    for (const route of routes) {
      if (route.method !== method) continue;
      const m = pathname.match(route.regex);
      if (!m) continue;
      const params = {};
      route.params.forEach((name, i) => { params[name] = decodeURIComponent(m[i + 1]); });
      let body = {};
      if (method === 'POST' || method === 'PUT' || method === 'PATCH') {
        try { body = await readJsonBody(req); }
        catch (err) { return send(res, 400, { error: err.message }); }
      }
      await route.handler(store, { params, query, body }, req, res);
      return true;
    }
    return false;
  }

  return { handle, routes: routes.map((r) => ({ method: r.method, path: r.regex.source })) };
}

module.exports = { createRouter, serveStatic, send };