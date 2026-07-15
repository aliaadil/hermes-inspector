/**
 * Hermes Inspector — Dashboard Plugin
 *
 * Two-pane view backed by /api/plugins/hermes-inspector/.
 * Uses the host dashboard's plugin SDK (window.__HERMES_PLUGIN_SDK__)
 * for React + shadcn primitives; ships its own minimal styles in
 * dist/style.css so the columns render predictably regardless of theme.
 *
 * Endpoints consumed (mounted by dashboard/plugin_api.py):
 *   GET  /health
 *   GET  /api/docs
 *   GET  /api/docs/:id
 *   GET  /api/board
 *   POST /api/board/move
 */
(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) {
    // Older host dashboards without the SDK — fail quiet so we don't
    // surface an uncaught error in the console. The user can still hit
    // the JSON endpoints directly.
    return;
  }

  const { React } = SDK;
  const h = React.createElement;
  const { useState, useEffect, useCallback, useMemo, useRef } = SDK.hooks;
  const { Button, Input, Label, Badge, Card, CardContent, Select, SelectOption } = SDK.components;
  const { cn, timeAgo } = SDK.utils;

  // The dashboard mounts each plugin's tab with this exact class.
  // Custom styles in dist/style.css target it.
  const ROOT_CLASS = "hermes-inspector-root";

  const COLUMNS = ["ready", "running", "blocked", "review", "done"];
  const COLUMN_LABELS = {
    ready: "Ready",
    running: "Running",
    blocked: "Blocked",
    review: "Review",
    done: "Done",
  };

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  async function fetchJson(url, opts) {
    const res = await fetch(url, Object.assign({ headers: { "content-type": "application/json" } }, opts || {}));
    let body = null;
    try { body = await res.json(); } catch (_) { body = null; }
    return { ok: res.ok, status: res.status, body };
  }

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" })[c];
    });
  }

  // ---------------------------------------------------------------------------
  // Board column
  // ---------------------------------------------------------------------------

  function Column(props) {
    const { title, cards, onMove } = props;
    const [over, setOver] = useState(false);
    return h(
      "div",
      {
        className: cn("hermes-inspector-column", over && "is-over"),
        onDragOver: function (e) { e.preventDefault(); setOver(true); },
        onDragLeave: function () { setOver(false); },
        onDrop: function (e) {
          e.preventDefault();
          setOver(false);
          const id = e.dataTransfer.getData("text/card-id");
          if (id) onMove(id, title);
        },
      },
      h("div", { className: "hermes-inspector-column-header" },
        h("span", null, COLUMN_LABELS[title] || title),
        h("span", { className: "hermes-inspector-column-count" }, cards.length)
      ),
      h("div", { className: "hermes-inspector-column-body" },
        cards.length === 0
          ? h("div", { className: "hermes-inspector-empty" }, "No cards")
          : cards.map(function (c) {
              return h(
                "div",
                {
                  key: c.card_id,
                  className: "hermes-inspector-card",
                  draggable: true,
                  onDragStart: function (e) { e.dataTransfer.setData("text/card-id", c.card_id); },
                },
                h("div", { className: "hermes-inspector-card-title" }, c.title || c.card_id),
                c.assignee && h("div", { className: "hermes-inspector-card-meta" }, "@" + c.assignee),
                c.body && h("div", { className: "hermes-inspector-card-body" }, c.body.slice(0, 160))
              );
            })
      )
    );
  }

  // ---------------------------------------------------------------------------
  // Docs pane
  // ---------------------------------------------------------------------------

  function DocsPane(props) {
    const { docs } = props;
    const [filter, setFilter] = useState("");
    const [openId, setOpenId] = useState(null);
    const [openDoc, setOpenDoc] = useState(null);

    const filtered = useMemo(function () {
      if (!filter) return docs;
      const needle = filter.toLowerCase();
      return docs.filter(function (d) {
        return (d.title || "").toLowerCase().includes(needle)
          || (d.task_id || "").toLowerCase().includes(needle)
          || (d.source || "").toLowerCase().includes(needle);
      });
    }, [docs, filter]);

    const onOpen = useCallback(async function (id) {
      setOpenId(id);
      const res = await fetchJson("/api/plugins/hermes-inspector/api/docs/" + encodeURIComponent(id));
      setOpenDoc(res.body);
    }, []);

    return h(
      "div",
      { className: "hermes-inspector-docs" },
      h("div", { className: "hermes-inspector-docs-toolbar" },
        h(Input, {
          placeholder: "Filter docs…",
          value: filter,
          onChange: function (e) { setFilter(e.target.value); },
        })
      ),
      openId && openDoc && h(
        "div",
        { className: "hermes-inspector-doc-detail" },
        h("div", { className: "hermes-inspector-doc-detail-header" },
          h("h3", null, openDoc.title || openDoc.id),
          h(Button, { onClick: function () { setOpenId(null); setOpenDoc(null); }, outlined: true }, "Close")
        ),
        h("pre", { className: "hermes-inspector-doc-content" }, openDoc.content || "")
      ),
      h("div", { className: "hermes-inspector-doc-list" },
        filtered.length === 0
          ? h("div", { className: "hermes-inspector-empty" }, "No docs")
          : filtered.map(function (d) {
              return h(
                "div",
                {
                  key: d.id,
                  className: "hermes-inspector-doc-row",
                  onClick: function () { onOpen(d.id); },
                },
                h("div", { className: "hermes-inspector-doc-title" }, d.title || d.id),
                h("div", { className: "hermes-inspector-doc-meta" },
                  h(Badge, null, d.source || "note"),
                  " ",
                  h("span", null, d.task_id),
                  " ",
                  h("span", { className: "hermes-inspector-time" }, d.created_at ? timeAgo(d.created_at) : "")
                )
              );
            })
      )
    );
  }

  // ---------------------------------------------------------------------------
  // Top-level Inspector view
  // ---------------------------------------------------------------------------

  function InspectorView() {
    const [cards, setCards] = useState([]);
    const [docs, setDocs] = useState([]);
    const [error, setError] = useState(null);

    const refresh = useCallback(async function () {
      try {
        const [b, d] = await Promise.all([
          fetchJson("/api/plugins/hermes-inspector/api/board"),
          fetchJson("/api/plugins/hermes-inspector/api/docs?limit=200"),
        ]);
        if (b.ok && b.body && Array.isArray(b.body.cards)) setCards(b.body.cards);
        if (d.ok && d.body && Array.isArray(d.body.docs)) setDocs(d.body.docs);
        setError(null);
      } catch (e) {
        setError(String(e));
      }
    }, []);

    useEffect(function () {
      refresh();
      const t = setInterval(refresh, 5000);
      return function () { clearInterval(t); };
    }, [refresh]);

    const onMove = useCallback(async function (cardId, toColumn) {
      const res = await fetchJson(
        "/api/plugins/hermes-inspector/api/board/move",
        { method: "POST", body: JSON.stringify({ card_id: cardId, to_column: toColumn }) }
      );
      if (res.ok) refresh();
    }, [refresh]);

    const grouped = useMemo(function () {
      const g = {};
      COLUMNS.forEach(function (c) { g[c] = []; });
      cards.forEach(function (c) {
        const col = COLUMNS.includes(c.column) ? c.column : "ready";
        g[col].push(c);
      });
      return g;
    }, [cards]);

    return h(
      "div",
      { className: ROOT_CLASS },
      h("div", { className: "hermes-inspector-toolbar" },
        h("h2", null, "Inspector"),
        h(Button, { onClick: refresh, outlined: true }, "Refresh"),
        error && h("span", { className: "hermes-inspector-error" }, error)
      ),
      h("div", { className: "hermes-inspector-grid" },
        h("div", { className: "hermes-inspector-board" },
          COLUMNS.map(function (col) {
            return h(Column, {
              key: col,
              title: col,
              cards: grouped[col],
              onMove: onMove,
            });
          })
        ),
        h(DocsPane, { docs: docs })
      )
    );
  }

  // The SDK exposes a mount function that the dashboard calls after
  // loading our bundle. Fall back to inserting a placeholder if the
  // SDK predates that helper.
  if (typeof SDK.mount === "function") {
    SDK.mount(InspectorView);
  } else if (typeof SDK.render === "function") {
    SDK.render(InspectorView, document.currentScript ? document.currentScript.parentElement : document.body);
  } else {
    // Last-resort: render into a known element id.
    const target = document.getElementById("hermes-inspector-root");
    if (target && SDK.render) SDK.render(InspectorView, target);
  }
})();