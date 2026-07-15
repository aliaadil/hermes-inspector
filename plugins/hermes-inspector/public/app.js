// Hermes Inspector dashboard — vanilla JS, no build step.
//
// Responsibilities:
//  - Render the kanban board grouped by column with drag-and-drop reordering.
//  - Render the docs feed newest-first, with expand-to-view on demand.
//  - Top-bar filters: text (title + content), date range, source task.
//    Filter state persists in localStorage between visits.
//  - Poll /api/board + /api/docs every 5s; pause when the tab is hidden.
//  - Manual refresh button.
//
// All API endpoints already exist on the host (see ../src/router.js).
// This file only renders; it never reaches into the store directly.

(function () {
  'use strict';

  const COLUMNS = ['triage', 'in_progress', 'review', 'done'];
  const COLUMN_LABELS = {
    triage: 'Triage',
    in_progress: 'In Progress',
    review: 'Review',
    done: 'Done',
  };
  // Maps the dashboard's simplified columns to the underlying store columns
  // we read from /api/board. Cards whose column is none of these land in
  // triage by default — keeps the four-pane UI uncluttered.
  const COLUMN_SOURCE = {
    triage: ['triage', 'todo', 'ready'],
    in_progress: ['in_progress', 'running'],
    review: ['review', 'blocked'],
    done: ['done'],
  };

  const POLL_MS = 5000;
  const STORAGE_KEY = 'hermes-inspector:filters:v1';

  // ---- State ----
  const state = {
    board: [],          // raw cards from /api/board
    docs: [],           // raw doc summaries from /api/docs
    expandedDocs: new Set(),   // doc ids whose content is loaded
    docContent: new Map(),     // id -> full content (lazy)
    filters: loadFilters(),
    lastFetch: null,
    inflight: false,
  };

  // ---- DOM ----
  const $ = (id) => document.getElementById(id);

  // ---- Utils ----
  function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }
  function fmtDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.toISOString().slice(0, 16).replace('T', ' ');
  }
  function debounce(fn, ms) {
    let t = null;
    return function (...args) {
      if (t) clearTimeout(t);
      t = setTimeout(() => fn.apply(this, args), ms);
    };
  }

  async function fetchJson(url, opts) {
    const res = await fetch(url, opts);
    const text = await res.text();
    let body; try { body = JSON.parse(text); } catch { body = text; }
    return { ok: res.ok, status: res.status, body };
  }

  // ---- Filters (localStorage) ----
  function loadFilters() {
    const defaults = { search: '', dateFrom: '', dateTo: '', sourceTask: '', autoRefresh: true };
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return defaults;
      const parsed = JSON.parse(raw);
      return Object.assign({}, defaults, parsed);
    } catch (_) { return defaults; }
  }
  function saveFilters() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state.filters)); }
    catch (_) { /* localStorage disabled — fine, we just won't persist */ }
  }

  // ---- Data loading ----
  async function loadAll() {
    if (state.inflight) return;
    state.inflight = true;
    try {
      const [board, docs] = await Promise.all([
        fetchJson('/api/board'),
        fetchJson('/api/docs?limit=200'),
      ]);
      state.board = (board.body && board.body.cards) || [];
      state.docs = (docs.body && docs.body.docs) || [];
      state.lastFetch = new Date();
      populateSourceTaskFilter();
      render();
      $('status').textContent = `connected · ${state.lastFetch.toISOString()}`;
      $('status').className = 'status ok';
    } catch (err) {
      $('status').textContent = `error: ${err.message}`;
      $('status').className = 'status err';
    } finally {
      state.inflight = false;
    }
  }

  // ---- Source-task dropdown: populated lazily from doc rows. ----
  function populateSourceTaskFilter() {
    const sel = $('f-source-task');
    const current = state.filters.sourceTask;
    const ids = Array.from(new Set(state.docs.map((d) => d.task_id).filter(Boolean))).sort();
    // Only rebuild if the set changed — avoids losing focus while typing.
    const existing = Array.from(sel.options).slice(1).map((o) => o.value);
    const same = existing.length === ids.length && existing.every((v, i) => v === ids[i]);
    if (!same) {
      sel.innerHTML = '<option value="">all tasks</option>' +
        ids.map((id) => `<option value="${escapeHtml(id)}">${escapeHtml(id)}</option>`).join('');
    }
    sel.value = current && ids.includes(current) ? current : '';
    if (sel.value !== state.filters.sourceTask) {
      state.filters.sourceTask = sel.value;
      saveFilters();
    }
  }

  // ---- Filtering helpers ----
  function cardMatchesSearch(card) {
    const q = state.filters.search.trim().toLowerCase();
    if (!q) return true;
    return (card.title || '').toLowerCase().includes(q)
      || (card.body || '').toLowerCase().includes(q)
      || (card.card_id || '').toLowerCase().includes(q);
  }
  function docMatchesSearch(doc) {
    const q = state.filters.search.trim().toLowerCase();
    if (!q) return true;
    return (doc.title || '').toLowerCase().includes(q)
      || (doc.task_id || '').toLowerCase().includes(q);
  }
  function docMatchesDate(doc) {
    if (!state.filters.dateFrom && !state.filters.dateTo) return true;
    const t = doc.created_at ? new Date(doc.created_at).getTime() : NaN;
    if (isNaN(t)) return false;
    if (state.filters.dateFrom) {
      const from = new Date(state.filters.dateFrom + 'T00:00:00Z').getTime();
      if (t < from) return false;
    }
    if (state.filters.dateTo) {
      // inclusive end-of-day
      const to = new Date(state.filters.dateTo + 'T23:59:59Z').getTime();
      if (t > to) return false;
    }
    return true;
  }
  function docMatchesSource(doc) {
    if (!state.filters.sourceTask) return true;
    return doc.task_id === state.filters.sourceTask;
  }

  function filteredCards() {
    return state.board.filter(cardMatchesSearch);
  }
  function filteredDocs() {
    return state.docs.filter((d) =>
      docMatchesSearch(d) && docMatchesDate(d) && docMatchesSource(d));
  }

  // ---- Render: board ----
  function bucketColumn(card) {
    const col = card.column;
    for (const [bucket, sources] of Object.entries(COLUMN_SOURCE)) {
      if (sources.includes(col)) return bucket;
    }
    return 'triage';
  }

  function renderBoard() {
    const root = $('board');
    root.innerHTML = '';
    const cards = filteredCards();
    const buckets = Object.fromEntries(COLUMNS.map((c) => [c, []]));
    for (const card of cards) buckets[bucketColumn(card)].push(card);

    let total = 0;
    for (const col of COLUMNS) {
      const items = buckets[col];
      total += items.length;
      const wrap = document.createElement('div');
      wrap.className = 'column';
      wrap.dataset.column = col;

      const head = document.createElement('div');
      head.className = 'column-head';
      head.innerHTML =
        `<span class="column-title">${escapeHtml(COLUMN_LABELS[col])}</span>` +
        `<span class="column-count">${items.length}</span>`;
      wrap.appendChild(head);

      const list = document.createElement('div');
      list.className = 'column-list';
      list.dataset.column = col;

      if (items.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'empty';
        empty.textContent = 'no cards';
        list.appendChild(empty);
      } else {
        for (const card of items) list.appendChild(renderCard(card));
      }

      // Drop target wiring.
      list.addEventListener('dragover', onDragOver);
      list.addEventListener('dragleave', onDragLeave);
      list.addEventListener('drop', onDrop);

      wrap.appendChild(list);
      root.appendChild(wrap);
    }

    $('board-count').textContent = `${total} card${total === 1 ? '' : 's'}`;
    if (cards.length === 0 && state.board.length > 0) {
      $('board-count').textContent += ` (filtered from ${state.board.length})`;
    }
  }

  function renderCard(card) {
    const el = document.createElement('div');
    el.className = 'card';
    el.draggable = true;
    el.dataset.cardId = card.card_id;
    el.innerHTML =
      `<div class="card-title">${escapeHtml(card.title || '(untitled)')}</div>` +
      `<div class="card-meta">` +
        `<span class="card-id">${escapeHtml(card.card_id)}</span>` +
        (card.assignee ? `<span class="card-assignee">@${escapeHtml(card.assignee)}</span>` : '') +
      `</div>`;
    el.addEventListener('dragstart', onDragStart);
    el.addEventListener('dragend', onDragEnd);
    return el;
  }

  // ---- Drag and drop ----
  let draggingId = null;

  function onDragStart(ev) {
    draggingId = ev.currentTarget.dataset.cardId;
    ev.currentTarget.classList.add('dragging');
    ev.dataTransfer.effectAllowed = 'move';
    // Some browsers require data to fire drop.
    try { ev.dataTransfer.setData('text/plain', draggingId); } catch (_) {}
  }
  function onDragEnd(ev) {
    ev.currentTarget.classList.remove('dragging');
    draggingId = null;
    document.querySelectorAll('.column-list.drag-over')
      .forEach((n) => n.classList.remove('drag-over'));
  }
  function onDragOver(ev) {
    if (!draggingId) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = 'move';
    ev.currentTarget.classList.add('drag-over');
  }
  function onDragLeave(ev) {
    // Only clear if the pointer actually left the list (not entered a child).
    if (ev.currentTarget.contains(ev.relatedTarget)) return;
    ev.currentTarget.classList.remove('drag-over');
  }
  async function onDrop(ev) {
    ev.preventDefault();
    ev.currentTarget.classList.remove('drag-over');
    if (!draggingId) return;
    const targetColumn = ev.currentTarget.dataset.column;
    const card = state.board.find((c) => c.card_id === draggingId);
    if (!card || bucketColumn(card) === targetColumn) return;

    const sourceCol = card.column;
    // Optimistic update: change locally, then sync to server.
    card.column = targetColumn;
    render();
    toast(`moving ${draggingId} → ${COLUMN_LABELS[targetColumn]}...`);

    const { ok, status, body } = await fetchJson('/api/board/move', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ card_id: draggingId, to_column: targetColumn }),
    });
    if (!ok) {
      // Roll back on failure.
      card.column = sourceCol;
      render();
      toast(`move failed (${status}): ${body && body.message || 'unknown error'}`, 'err');
      return;
    }
    toast(`moved ${draggingId} → ${COLUMN_LABELS[targetColumn]}`, 'ok');
  }

  // ---- Render: docs ----
  function renderDocs() {
    const root = $('docs');
    root.innerHTML = '';
    const docs = filteredDocs();
    if (docs.length === 0) {
      const li = document.createElement('li');
      li.className = 'empty';
      li.textContent = state.docs.length === 0
        ? 'no docs yet — emitted docs will appear here'
        : 'no docs match the current filters';
      root.appendChild(li);
    } else {
      for (const doc of docs) root.appendChild(renderDocRow(doc));
    }
    let label = `${docs.length} doc${docs.length === 1 ? '' : 's'}`;
    if (docs.length !== state.docs.length) {
      label += ` (filtered from ${state.docs.length})`;
    }
    $('docs-count').textContent = label;
  }

  function renderDocRow(doc) {
    const li = document.createElement('li');
    li.className = 'doc';
    li.dataset.docId = doc.id;

    const expanded = state.expandedDocs.has(doc.id);
    const content = state.docContent.get(doc.id);

    li.innerHTML =
      `<div class="doc-head">` +
        `<button type="button" class="doc-toggle" aria-expanded="${expanded}">${expanded ? '▾' : '▸'}</button>` +
        `<span class="doc-title">${escapeHtml(doc.title || '(untitled)')}</span>` +
        `<span class="doc-meta">` +
          `<span class="doc-task">${escapeHtml(doc.task_id || 'unscoped')}</span>` +
          `<span class="doc-source">${escapeHtml(doc.source || '')}</span>` +
          `<span class="doc-date">${escapeHtml(fmtDate(doc.created_at))}</span>` +
        `</span>` +
      `</div>` +
      (expanded
        ? `<div class="doc-body" data-loading="${content ? 'false' : 'true'}">${
            content ? renderDocBody(content) : 'loading...'
          }</div>`
        : '');

    li.querySelector('.doc-toggle').addEventListener('click', () => toggleDoc(doc.id));
    li.querySelector('.doc-head').addEventListener('click', (ev) => {
      if (ev.target.closest('.doc-toggle')) return;
      toggleDoc(doc.id);
    });
    return li;
  }

  function renderDocBody(content) {
    // Light-weight markdown-ish rendering: paragraphs + code fences.
    // The DB stores the source the plugin emits; we don't want a full
    // markdown dep for the dashboard, so handle the common cases (``` blocks,
    // # headings, blank-line paragraphs) inline.
    const esc = escapeHtml(content);
    const out = [];
    const fenceRe = /```([\w-]*)\n([\s\S]*?)```/g;
    let last = 0;
    let m;
    while ((m = fenceRe.exec(esc)) !== null) {
      out.push(renderInline(esc.slice(last, m.index)));
      out.push(`<pre class="doc-code"><code>${m[2]}</code></pre>`);
      last = m.index + m[0].length;
    }
    out.push(renderInline(esc.slice(last)));
    return out.join('');
  }
  function renderInline(text) {
    return text
      .split(/\n{2,}/)
      .map((para) => {
        if (/^#{1,6} /.test(para)) {
          const h = para.match(/^(#{1,6}) (.+)$/m);
          return h ? `<h${h[1].length}>${h[2]}</h${h[1].length}>` : `<p>${para.replace(/\n/g, '<br>')}</p>`;
        }
        return `<p>${para.replace(/\n/g, '<br>')}</p>`;
      })
      .join('');
  }

  async function toggleDoc(id) {
    if (state.expandedDocs.has(id)) {
      state.expandedDocs.delete(id);
      renderDocs();
      return;
    }
    state.expandedDocs.add(id);
    if (!state.docContent.has(id)) {
      const { ok, body } = await fetchJson('/api/docs/' + encodeURIComponent(id));
      if (ok && body && typeof body.content === 'string') {
        state.docContent.set(id, body.content);
      } else {
        state.docContent.set(id, '(failed to load)');
      }
    }
    renderDocs();
  }

  // ---- Toast ----
  let toastTimer = null;
  function toast(msg, kind) {
    const el = $('toast');
    el.textContent = msg;
    el.className = 'toast' + (kind ? ' ' + kind : '');
    el.hidden = false;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 2500);
  }

  // ---- Render dispatch ----
  function render() {
    renderBoard();
    renderDocs();
  }

  // ---- Filter wiring ----
  function bindFilters() {
    const search = $('f-search');
    const dateFrom = $('f-date-from');
    const dateTo = $('f-date-to');
    const sourceTask = $('f-source-task');
    const auto = $('f-auto');

    search.value = state.filters.search;
    dateFrom.value = state.filters.dateFrom;
    dateTo.value = state.filters.dateTo;
    auto.checked = !!state.filters.autoRefresh;

    const onChange = debounce(() => {
      state.filters.search = search.value;
      state.filters.dateFrom = dateFrom.value;
      state.filters.dateTo = dateTo.value;
      state.filters.sourceTask = sourceTask.value;
      state.filters.autoRefresh = auto.checked;
      saveFilters();
      render();
      restartPolling();
    }, 150);

    search.addEventListener('input', onChange);
    dateFrom.addEventListener('change', onChange);
    dateTo.addEventListener('change', onChange);
    sourceTask.addEventListener('change', onChange);
    auto.addEventListener('change', onChange);
  }

  // ---- Polling ----
  let pollHandle = null;
  function startPolling() {
    if (!state.filters.autoRefresh) return;
    if (document.hidden) return;
    if (pollHandle) return;
    pollHandle = setInterval(() => { loadAll(); }, POLL_MS);
  }
  function stopPolling() {
    if (pollHandle) { clearInterval(pollHandle); pollHandle = null; }
  }
  function restartPolling() {
    stopPolling();
    startPolling();
  }

  // ---- Init ----
  async function init() {
    bindFilters();
    $('btn-refresh').addEventListener('click', () => loadAll());

    // Pause polling when tab is hidden — saves battery and avoids hammering
    // the host when nobody's looking.
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) stopPolling();
      else { loadAll(); startPolling(); }
    });

    await loadAll();
    startPolling();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();