"""Regression guard: the headless CLI switch path must import no GUI modules.

GUI-09 / SD-07 / T-04-17 — Stream Deck plugin calls ``lolswitcher switch
<username>`` as a subprocess; the headless path must remain import-clean so
that the frozen exe can be invoked without a display and without loading the
WebView2 / customtkinter stack.

Tests:
1. ``main.py switch <bogus>`` exits with an integer code (0 or 1).
2. The subprocess imports NONE of: webview, customtkinter, tkinter,
   gui.webview_window, gui.js_api.
3. stdout is empty (--noconsole sets sys.stdout=None; print() is forbidden).
"""
from __future__ import annotations

import subprocess
import sys
import unittest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FORBIDDEN_MODULES = [
    "webview",
    "customtkinter",
    "tkinter",
    "gui.webview_window",
    "gui.js_api",
    "pystray",
]

_CHECK_SCRIPT = """
import sys, os
sys.path.insert(0, os.path.abspath('.'))

# Intercept imports BEFORE main runs
_forbidden = {forbidden!r}
_violations = []

_real_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

import builtins
_orig_import = builtins.__import__

def _tracking_import(name, *args, **kwargs):
    for mod in _forbidden:
        if name == mod or name.startswith(mod + '.'):
            _violations.append(name)
    return _orig_import(name, *args, **kwargs)

builtins.__import__ = _tracking_import

try:
    import main as _main_module
    # Simulate: sys.argv = ['main.py', 'switch', '<bogus-user>']
    sys.argv = ['main.py', 'switch', '__bogus_user_for_test__']
    try:
        _main_module.main()
    except SystemExit:
        pass
finally:
    builtins.__import__ = _orig_import

if _violations:
    sys.stderr.write('FORBIDDEN IMPORTS: ' + ', '.join(_violations) + '\\n')
    sys.exit(2)
else:
    sys.exit(0)
""".format(forbidden=_FORBIDDEN_MODULES)


class TestHeadlessNoGuiImport(unittest.TestCase):
    """Verify that ``main.py switch`` imports no GUI/webview/tkinter modules."""

    def _run_switch(self, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
        """Run ``py main.py switch <bogus>`` in a fresh subprocess."""
        cmd = [
            sys.executable,
            "main.py",
            "switch",
            "__bogus_user_for_test__",
        ]
        if extra_args:
            cmd.extend(extra_args)
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=".",
            timeout=30,
        )

    def _run_import_check(self) -> subprocess.CompletedProcess:
        """Run the import-tracking script in a fresh subprocess."""
        return subprocess.run(
            [sys.executable, "-c", _CHECK_SCRIPT],
            capture_output=True,
            text=True,
            cwd=".",
            timeout=30,
        )

    def test_switch_exits_with_integer_code(self):
        """``main.py switch <bogus>`` must exit with an integer code (0 or 1), not a string."""
        result = self._run_switch()
        self.assertIn(
            result.returncode,
            (0, 1),
            f"Expected exit code 0 or 1, got {result.returncode}.\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}",
        )

    def test_switch_stdout_empty(self):
        """``main.py switch <bogus>`` must produce no stdout output.

        PyInstaller --noconsole sets sys.stdout=None; any print() crashes the
        headless path.  We assert empty stdout here as a proxy guard (Pitfall 1).
        """
        result = self._run_switch()
        self.assertEqual(
            result.stdout,
            "",
            f"Unexpected stdout from switch branch: {result.stdout!r}",
        )

    def test_switch_imports_no_gui_modules(self):
        """The switch CLI path must import none of the GUI/webview/tkinter modules.

        Tracked modules: {mods}
        """.format(mods=", ".join(_FORBIDDEN_MODULES))
        result = self._run_import_check()
        self.assertEqual(
            result.returncode,
            0,
            f"Forbidden GUI/webview modules were imported during 'switch' branch.\n"
            f"stderr: {result.stderr!r}\nstdout: {result.stdout!r}",
        )

    def test_switch_importtime_no_forbidden(self):
        """Verify forbidden modules absent from -X importtime output."""
        result = subprocess.run(
            [sys.executable, "-X", "importtime", "main.py", "switch", "__bogus_user__"],
            capture_output=True,
            text=True,
            cwd=".",
            timeout=30,
        )
        # -X importtime writes to stderr; each line ends in the dotted module name:
        # "import time:  <self ns> | <cumulative ns> | <module>"
        imported = set()
        for line in result.stderr.lower().splitlines():
            if "|" in line:
                imported.add(line.rsplit("|", 1)[1].strip())
        for mod in _FORBIDDEN_MODULES:
            hits = [m for m in imported if m == mod or m.startswith(mod + ".")]
            self.assertFalse(
                hits,
                f"Module '{mod}' was imported in the switch branch (importtime): {hits}",
            )


if __name__ == "__main__":
    unittest.main()
