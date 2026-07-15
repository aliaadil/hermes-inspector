"""Regression: the bundled dashboard plugin must register itself with the
current Hermes host contract.

The host dashboard ships a `window.__HERMES_PLUGINS__.register(slug, Component)`
hook that every bundled plugin calls once during script eval. Without that
call, the host renders an empty page and reports
``The plugin's script did not call register()`` — exactly the regression that
landed when the inspector plugin was re-bundled against an older SDK
contract.

This test loads ``dashboard/dist/index.js`` under a stub ``window`` (no DOM,
no React) and asserts the exact call shape used by the canonical host
bundles (see ``/opt/hermes/plugins/kanban/dashboard/dist/index.js`` and
``/opt/hermes/plugins/hermes-achievements/dashboard/dist/index.js``).

If you re-bundle the plugin against a different SDK, update this test
*and* the line in ``dist/index.js`` that performs the register call.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLE = REPO_ROOT / "dashboard" / "dist" / "index.js"

# Node-side stub: defines the SDK and the Plugins host, then evals the
# bundle. The bundle will call Plugins.register(...) during eval; we
# stringify every (slug, name) it touched and emit as JSON on stdout.
NODE_DRIVER = r"""
'use strict';
const captured = [];
const SDK = { React: {}, hooks: {}, components: {}, utils: {} };
const Plugins = {
  register(slug, Component) {
    captured.push({
      slug,
      name: Component && Component.name ? Component.name : '<anonymous>',
    });
  },
};
const window = { __HERMES_PLUGIN_SDK__: SDK, __HERMES_PLUGINS__: Plugins };
// Minimal globals the bundle may touch at registration time:
const document = {
  getElementById: () => null,
  currentScript: null,
  createElement: () => ({ style: {} }),
};
const fetch = () => Promise.resolve({ ok: false, status: 0, json: () => null });
const React = SDK.React;
const console = { log() {}, warn() {}, error() {} };

try {
  // The bundle is an IIFE that uses window/document/fetch — we evaluate
  // it via Function() so those globals are in scope under those names.
  const src = require('fs').readFileSync(process.argv[2], 'utf8');
  // eslint-disable-next-line no-new-func
  const fn = new Function('window', 'document', 'fetch', 'SDK', 'Plugins', 'React', 'console', src);
  fn(window, document, fetch, SDK, Plugins, React, console);
  process.stdout.write(JSON.stringify({ ok: true, registered: captured }));
} catch (err) {
  process.stdout.write(JSON.stringify({
    ok: false,
    error: String(err && err.stack || err),
    registered: captured,
  }));
  process.exit(1);
}
"""


class DashboardBundleRegisterTests(unittest.TestCase):
    """Bundle registers against window.__HERMES_PLUGINS__.register()."""

    def setUp(self) -> None:
        if not BUNDLE.exists():
            self.skipTest(f"bundle not found: {BUNDLE}")
        with tempfile.TemporaryDirectory() as tmp:
            driver_path = Path(tmp) / "driver.js"
            driver_path.write_text(NODE_DRIVER)
            out = subprocess.run(
                ["node", str(driver_path), str(BUNDLE)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        self._stdout = out.stdout
        self._stderr = out.stderr
        self._returncode = out.returncode
        # The driver may exit 0 on success or 1 on eval error — both encode
        # a JSON result on stdout. Parse defensively.
        try:
            self._result = json.loads(self._stdout)
        except json.JSONDecodeError as e:
            self.fail(
                f"node driver produced unparseable output "
                f"(rc={self._returncode}, stdout={self._stdout!r}, "
                f"stderr={self._stderr!r}): {e}"
            )

    def test_bundle_loads_without_error(self):
        """Bundle evaluates cleanly under a minimal stub window."""
        self.assertTrue(
            self._result.get("ok"),
            f"bundle did not load under stub window: "
            f"{self._result.get('error')!r}",
        )

    def test_bundle_registers_with_current_sdk_contract(self):
        """Bundle calls Plugins.register with the inspector slug.

        The current Hermes host contract is::

            window.__HERMES_PLUGINS__.register("hermes-inspector", <Component>)

        Earlier bundles used ``SDK.mount(...)`` / ``SDK.render(...)`` which
        the current host no longer recognises, producing an empty plugin
        tab. Pin the new contract here so the regression cannot return.
        """
        registered = self._result.get("registered") or []
        matches = [r for r in registered if r.get("slug") == "hermes-inspector"]
        self.assertEqual(
            len(matches), 1,
            f"expected exactly one Plugins.register call with slug "
            f"'hermes-inspector', got {registered!r}",
        )
        # The component passed to register must be a named function so the
        # host can introspect it; an arrow / anonymous component is a
        # visible regression in dev tooling.
        component_name = matches[0].get("name", "")
        self.assertTrue(
            component_name and component_name != "<anonymous>",
            f"register() was called with an anonymous component "
            f"({component_name!r}); expected a named function so the "
            f"host can introspect it",
        )


if __name__ == "__main__":
    unittest.main()
