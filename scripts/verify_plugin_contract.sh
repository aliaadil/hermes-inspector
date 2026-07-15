#!/usr/bin/env bash
# Acceptance script — re-run the exact QA scenario from scratch.
#
# Sets up an isolated HERMES_HOME, installs the plugin from a local
# file:// URL with --enable, then runs the integration test that
# exercises the real Hermes plugin contract end-to-end.
#
# Usage: bash scripts/verify_plugin_contract.sh
#
# Exit code 0 on success, non-zero on any failure.

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_BIN="${HERMES_BIN:-/opt/hermes/bin/hermes}"
HERMES_PY="${HERMES_PY:-/opt/hermes/.venv/bin/python}"
HERMES_HOME_DEFAULT="${HERMES_HOME:-$(mktemp -d -t hermes-inspector-verify.XXXXXX)}"
MOCK_AUTH="${MOCK_AUTH:-true}"

if [[ -z "${HERMES_HOME:-}" ]]; then
  export HERMES_HOME="$HERMES_HOME_DEFAULT"
fi
mkdir -p "$HERMES_HOME"

echo "== verify_plugin_contract.sh =="
echo "REPO_ROOT  : $REPO_ROOT"
echo "HERMES_HOME: $HERMES_HOME"
echo "HERMES_BIN : $HERMES_BIN"
echo "HERMES_PY  : $HERMES_PY"
echo

# Clean previous install if any.
rm -rf "$HERMES_HOME/plugins/hermes-inspector"

# 1) Install with --enable into isolated HERMES_HOME.
echo "== Step 1: hermes plugins install (file://, --enable) =="
"$HERMES_BIN" --version
"$HERMES_BIN" plugins install "file://$REPO_ROOT" --enable --force
echo

# 2) Confirm `hermes plugins list --plain --no-bundled` shows it.
echo "== Step 2: hermes plugins list =="
PLUGINS_OUT="$("$HERMES_BIN" plugins list --plain --no-bundled 2>&1)"
echo "$PLUGINS_OUT"
echo "$PLUGINS_OUT" | grep -q "hermes-inspector" \
  || { echo "FAIL: hermes-inspector not listed"; exit 2; }
echo

# 3) Run the integration test that drives real Hermes hooks.
echo "== Step 3: integration test (real PluginManager + real kanban_db) =="
"$HERMES_PY" "$REPO_ROOT/tests/integration_test.py"
INT_EXIT=$?
if [[ $INT_EXIT -ne 0 ]]; then
  echo "FAIL: integration_test.py exited $INT_EXIT"
  exit $INT_EXIT
fi
echo

# 4) Run the unit test suite to confirm no regressions.
echo "== Step 4: Python unit tests =="
"$HERMES_PY" -m unittest discover "$REPO_ROOT/tests" -v 2>&1 | tail -5
echo

# 5) Run the JS test suite (66 / 82 tests depending on growth).
if [[ -d "$REPO_ROOT/plugins/hermes-inspector" ]]; then
  echo "== Step 5: JS plugin tests (npm test) =="
  (
    cd "$REPO_ROOT/plugins/hermes-inspector"
    if [[ ! -d node_modules ]]; then
      npm install --no-audit --no-fund --silent
    fi
    npm test 2>&1 | tail -20
  )
  echo
fi

echo "== ALL STEPS PASSED =="
echo "HERMES_HOME left in place at: $HERMES_HOME"
echo "Inspect the inspector store at: $HERMES_HOME/inspector-data/inspector.db"