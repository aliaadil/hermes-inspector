# ADR-0003: Dashboard UI layout

Status: accepted
Date: 2026-07-10

## Context

The scaffold ADR (#0002) established the HTTP API surface and a placeholder
index.html. The dashboard task (t_49f84c71) asked for a real, organized UI
that exposes the live kanban board and the doc feed in a single page.

## Decision

Ship a single-page vanilla-JS dashboard with two panes:

- **Left — Board.** Four columns (Triage / In Progress / Review / Done)
  rendered from `GET /api/board`. Underlying store columns are mapped onto
  these four buckets (`triage ← todo|ready|triage`, `in_progress ← running`,
  `review ← blocked`, `done ← done`) so the kanban's internal column set can
  keep growing without forcing a UI redesign. Cards are draggable; drop fires
  `POST /api/board/move` and the UI optimistically updates with a rollback
  if the server rejects.
- **Right — Docs.** A list rendered from `GET /api/docs?limit=200`,
  newest-first. Each row has a `▸ / ▾` toggle that lazily fetches the full
  content from `GET /api/docs/:id`. Content rendering handles paragraphs,
  `#` headings, and ``` fenced code blocks inline — no markdown dependency.

Top-bar filters:

- **Search** matches card title + body + id (left pane) and doc title +
  task id (right pane). Live, debounced.
- **Date range** filters docs by `created_at`.
- **Source task** dropdown is auto-populated from the docs feed; selecting
  one narrows both panes (the board filters on `card_id` substring via the
  existing search field; docs filter on exact `task_id` match).

Filter state persists in `localStorage` under the key
`hermes-inspector:filters:v1`.

Auto-refresh: `setInterval(loadAll, 5000)` when `auto-refresh` is checked and
the tab is visible; polling pauses when the tab is hidden and resumes on
focus.

## Why no framework

The full bundle is **25KB** (HTML + CSS + JS). Adding React + a build step
would push this past 200KB and require a toolchain we don't otherwise need
for a single-page admin tool. The vanilla choice also keeps the dashboard
debuggable in DevTools without source maps.

## Bundle budget

Hard cap: 200KB. Current: 25KB. Headroom is intentional — anything that
pushes us over 50KB should trigger a rethink.

## Verification

- `npm test` runs 30 new UI assertions on top of the 52 prior smoke +
  integration tests, all passing.
- Manual browser check: page renders without console errors, drag-and-drop
  via the move endpoint persists, doc expand renders markdown, search filter
  narrows both panes, empty states render for columns with zero cards.