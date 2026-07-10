// Tiny single-purpose YAML loader for the plugin manifest.
//
// We use this instead of pulling in a `js-yaml` / `yaml` dep because the
// manifest is small, controlled, and written by us — so a hand-rolled parser
// is fine and keeps the plugin's dependency surface at "better-sqlite3 only".
//
// Supports the subset of YAML the manifest uses:
//   * key: value           (string, number, boolean, null)
//   * key: [a, b, c]       (inline list of scalars)
//   * key:                 (block-list, indented)
//     - item
//     - item
//   * # comments
// Quoted strings are unwrapped; unquoted scalars are trimmed.
//
// If the manifest ever grows beyond what this handles, swap to `js-yaml` and
// delete this file.

'use strict';

const fs = require('fs');

function stripComment(line) {
  // Naive: drop everything after the first '#' unless inside quotes.
  let inQ = null;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQ) { if (ch === inQ) inQ = null; continue; }
    if (ch === '"' || ch === "'") { inQ = ch; continue; }
    if (ch === '#') return line.slice(0, i);
  }
  return line;
}

function unquote(s) {
  s = s.trim();
  if ((s.startsWith('"') && s.endsWith('"')) ||
      (s.startsWith("'") && s.endsWith("'"))) {
    return s.slice(1, -1);
  }
  return s;
}

function coerceScalar(s) {
  s = unquote(s);
  if (s === '') return null;
  if (s === 'true') return true;
  if (s === 'false') return false;
  if (s === 'null' || s === '~') return null;
  if (/^-?\d+$/.test(s)) return Number(s);
  if (/^-?\d+\.\d+$/.test(s)) return Number(s);
  return s;
}

function parseInlineList(s) {
  // [a, b, "c d"] — supports quoted items with spaces.
  s = s.trim();
  if (!s.startsWith('[') || !s.endsWith(']')) return null;
  const inner = s.slice(1, -1).trim();
  if (!inner) return [];
  const items = [];
  let buf = '';
  let inQ = null;
  for (let i = 0; i < inner.length; i++) {
    const ch = inner[i];
    if (inQ) { buf += ch; if (ch === inQ) inQ = null; continue; }
    if (ch === '"' || ch === "'") { inQ = ch; buf += ch; continue; }
    if (ch === ',') { items.push(coerceScalar(buf.trim())); buf = ''; continue; }
    buf += ch;
  }
  if (buf.trim() !== '') items.push(coerceScalar(buf.trim()));
  return items;
}

function load(text) {
  const lines = text.split(/\r?\n/);
  // Drop blanks/comments, capture each meaningful line's indent.
  const records = [];
  for (const raw of lines) {
    const stripped = stripComment(raw);
    if (stripped.trim() === '') continue;
    const indent = stripped.match(/^ */)[0].length;
    records.push({ indent, body: stripped.slice(indent) });
  }

  const root = {};
  // Stack of { indent, container, kind: 'object' | 'list' }
  const stack = [{ indent: -1, container: root, kind: 'object' }];

  for (const rec of records) {
    // Pop frames whose indent is greater or equal to ours.
    while (stack.length > 1 && stack[stack.length - 1].indent >= rec.indent) stack.pop();

    const top = stack[stack.length - 1];
    const body = rec.body;

    if (body.startsWith('- ')) {
      // List item under the top container.
      const itemBody = body.slice(2);
      const ci = itemBody.indexOf(':');
      if (ci > 0 && !itemBody.slice(0, ci).includes('"')) {
        // Inline map inside the list item: { key: val, key: val }
        const obj = {};
        const segments = splitTopLevelCommas(itemBody);
        for (const seg of segments) {
          const c = seg.indexOf(':');
          if (c < 0) continue;
          const k = seg.slice(0, c).trim();
          const v = seg.slice(c + 1).trim();
          obj[k] = parseValue(v);
        }
        top.container.push(obj);
      } else {
        top.container.push(parseValue(itemBody));
      }
      continue;
    }

    const ci = body.indexOf(':');
    if (ci < 0) continue;
    const key = body.slice(0, ci).trim();
    const value = body.slice(ci + 1).trim();

    if (value === '') {
      // Block follow-up on subsequent lines.
      // Decide object vs list by peeking the next record.
      const next = records[records.indexOf(rec) + 1];
      if (next && next.indent > rec.indent && next.body.startsWith('- ')) {
        top.container[key] = [];
        stack.push({ indent: rec.indent, container: top.container[key], kind: 'list' });
      } else if (next && next.indent > rec.indent) {
        top.container[key] = {};
        stack.push({ indent: rec.indent, container: top.container[key], kind: 'object' });
      } else {
        top.container[key] = null;
      }
    } else {
      top.container[key] = parseValue(value);
    }
  }

  return root;
}

function splitTopLevelCommas(s) {
  const out = [];
  let buf = '';
  let inQ = null;
  for (const ch of s) {
    if (inQ) { buf += ch; if (ch === inQ) inQ = null; continue; }
    if (ch === '"' || ch === "'") { inQ = ch; buf += ch; continue; }
    if (ch === ',') { out.push(buf.trim()); buf = ''; continue; }
    buf += ch;
  }
  if (buf.trim()) out.push(buf.trim());
  return out;
}

function parseValue(s) {
  const list = parseInlineList(s);
  if (list !== null) return list;
  return coerceScalar(s);
}

module.exports = {
  load: (path) => load(fs.readFileSync(path, 'utf8')),
  parse: load,
};