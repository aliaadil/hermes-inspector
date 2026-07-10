// Hermes Inspector dashboard — vanilla JS, no build step.
const BASE = '';

async function fetchJson(url, opts) {
  const res = await fetch(BASE + url, opts);
  const text = await res.text();
  let body; try { body = JSON.parse(text); } catch { body = text; }
  return { ok: res.ok, status: res.status, body };
}

const COLUMNS = ['todo', 'ready', 'running', 'blocked', 'review', 'done'];

async function loadBoard() {
  const el = document.getElementById('board');
  el.innerHTML = '';
  const { body } = await fetchJson('/api/board');
  const grouped = Object.fromEntries(COLUMNS.map((c) => [c, []]));
  for (const card of body.cards || []) {
    (grouped[card.column] || grouped.ready).push(card);
  }
  for (const col of COLUMNS) {
    const wrap = document.createElement('div');
    wrap.className = 'column';
    wrap.innerHTML = `<h3>${col}</h3>`;
    for (const card of grouped[col]) {
      const c = document.createElement('div');
      c.className = 'card';
      c.innerHTML = `<div>${escapeHtml(card.title)}</div><div class="id">${card.card_id}</div>`;
      wrap.appendChild(c);
    }
    el.appendChild(wrap);
  }
}

async function loadDocs() {
  const ul = document.getElementById('docs');
  ul.innerHTML = '';
  const { body } = await fetchJson('/api/docs');
  for (const doc of (body.docs || []).slice(0, 25)) {
    const li = document.createElement('li');
    li.innerHTML = `<strong>${escapeHtml(doc.title)}</strong>
      <span class="id"> [${escapeHtml(doc.source)}] ${escapeHtml(doc.task_id)} · ${escapeHtml(doc.created_at)}</span>`;
    ul.appendChild(li);
  }
}

document.getElementById('move-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const fd = new FormData(ev.target);
  const out = document.getElementById('move-result');
  out.textContent = '...';
  const { ok, status, body } = await fetchJson('/api/board/move', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      card_id: fd.get('card_id'),
      to_column: fd.get('to_column'),
    }),
  });
  out.textContent = `${status} ${ok ? 'OK' : 'ERR'}  ${JSON.stringify(body, null, 2)}`;
  out.className = ok ? 'ok' : 'err';
  if (ok) loadBoard();
});

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

async function init() {
  try {
    await Promise.all([loadBoard(), loadDocs()]);
    document.getElementById('status').textContent = `connected · ${new Date().toISOString()}`;
  } catch (err) {
    document.getElementById('status').textContent = `error: ${err.message}`;
  }
}
init();
setInterval(() => { loadBoard(); loadDocs(); }, 5000);