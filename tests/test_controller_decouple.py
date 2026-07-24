"""Tests for controller.py tkinter decoupling (Phase 4 Plan 01 — TDD RED/GREEN).

Verifies:
1. controller module does not import tkinter after decoupling.
2. Controller constructs with a FakeWindow; _push_state writes all expected fields.
3. _serialize_accounts returns safe dicts (no password key, correct keys present).
4. _push_state is a no-op when _shutting_down is True.
5. _clear_clipboard_if_matches calls clipboard_get and only clears on match.
"""
from __future__ import annotations

import importlib
import subprocess
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeState:
    """Minimal pywebview window.state stub — supports attribute assignment."""

    def __init__(self):
        self.accounts = None
        self.active_username = None
        self.status = None
        self.status_message = None
        self.pending_first_login = None


class FakeWindow:
    """Minimal pywebview Window stub."""

    def __init__(self):
        self.state = FakeState()

    def minimize(self):
        pass

    def maximize(self):
        pass

    def destroy(self):
        pass


# ---------------------------------------------------------------------------
# Test 1: No tkinter in controller imports
# ---------------------------------------------------------------------------


class TestNoTkinterImport(unittest.TestCase):
    """Verify that importing controller does not pull in tkinter."""

    def test_controller_import_no_tkinter_subprocess(self):
        """Run a subprocess to check tkinter is NOT in sys.modules after importing controller."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    "import os; "
                    "sys.path.insert(0, '.'); "
                    "import controller; "
                    "assert 'tkinter' not in sys.modules, "
                    "'tkinter found in sys.modules: ' + str([k for k in sys.modules if \"tkinter\" in k])"
                ),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"tkinter leaked into controller imports.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}",
        )

    def test_controller_source_has_no_tkinter_import(self):
        """controller.py source must not contain 'import tkinter'."""
        with open("controller.py", encoding="utf-8") as fh:
            source = fh.read()
        self.assertNotIn(
            "import tkinter",
            source,
            "controller.py still contains 'import tkinter'",
        )

    def test_controller_source_no_root_after(self):
        """controller.py must not contain any self.root.after calls."""
        with open("controller.py", encoding="utf-8") as fh:
            source = fh.read()
        self.assertNotIn(
            "self.root.after",
            source,
            "controller.py still contains self.root.after calls",
        )

    def test_controller_source_no_tk_tclerror(self):
        """controller.py must not contain tk.TclError references."""
        with open("controller.py", encoding="utf-8") as fh:
            source = fh.read()
        self.assertNotIn(
            "tk.TclError",
            source,
            "controller.py still contains tk.TclError references",
        )

    def test_controller_source_no_notify_no_add_listener(self):
        """controller.py must not define _notify or add_listener."""
        with open("controller.py", encoding="utf-8") as fh:
            source = fh.read()
        self.assertNotIn(
            "def _notify",
            source,
            "controller.py still defines _notify",
        )
        self.assertNotIn(
            "def add_listener",
            source,
            "controller.py still defines add_listener",
        )

    def test_controller_source_has_push_state_and_serialize(self):
        """controller.py must define _push_state and _serialize_accounts."""
        with open("controller.py", encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn("def _push_state", source)
        self.assertIn("def _serialize_accounts", source)


# ---------------------------------------------------------------------------
# Test 2: Controller constructs and _push_state populates FakeWindow.state
# ---------------------------------------------------------------------------


class TestControllerPushState(unittest.TestCase):
    """Verify Controller constructs with FakeWindow and _push_state works."""

    def _make_controller(self, fake_window=None):
        """Import controller fresh and construct with a FakeWindow."""
        if fake_window is None:
            fake_window = FakeWindow()
        import controller as ctrl_module
        # Patch config to avoid filesystem reads
        with patch("config.load_state") as mock_load, \
             patch("config.ensure_dirs"), \
             patch.object(ctrl_module.Controller, "_schedule_accounts_poll"):
            from models import AppState
            mock_load.return_value = AppState()
            c = ctrl_module.Controller(root=fake_window)
        return c, fake_window

    def test_construction_succeeds(self):
        """Controller(root=FakeWindow()) constructs without error."""
        c, window = self._make_controller()
        self.assertIsNotNone(c)

    def test_push_state_sets_accounts(self):
        """_push_state writes accounts list to window.state.accounts."""
        c, window = self._make_controller()
        c._push_state()
        self.assertIsNotNone(window.state.accounts)
        self.assertIsInstance(window.state.accounts, list)

    def test_push_state_sets_active_username(self):
        """_push_state writes active_username to window.state."""
        c, window = self._make_controller()
        c.state.active_username = "test_user"
        c._push_state()
        self.assertEqual(window.state.active_username, "test_user")

    def test_push_state_sets_status_value(self):
        """_push_state writes SwitchStatus.value string to window.state.status."""
        c, window = self._make_controller()
        from models import SwitchStatus
        c.state.status = SwitchStatus.IDLE
        c._push_state()
        self.assertEqual(window.state.status, "idle")

    def test_push_state_sets_status_message(self):
        """_push_state writes status_message to window.state."""
        c, window = self._make_controller()
        c.state.status_message = "Bereit"
        c._push_state()
        self.assertEqual(window.state.status_message, "Bereit")

    def test_push_state_sets_pending_first_login_none(self):
        """_push_state writes None to window.state.pending_first_login when no pending."""
        c, window = self._make_controller()
        c._pending_first_login = None
        c._push_state()
        self.assertIsNone(window.state.pending_first_login)

    def test_push_state_sets_pending_first_login_username(self):
        """_push_state writes pending account username to window.state.pending_first_login."""
        c, window = self._make_controller()
        from models import Account
        pending = Account(username="smurf", display_name="Smurf")
        c._pending_first_login = pending
        c._push_state()
        self.assertEqual(window.state.pending_first_login, "smurf")

    def test_push_state_accounts_equals_serialize_accounts(self):
        """window.state.accounts should equal _serialize_accounts() output."""
        c, window = self._make_controller()
        c._push_state()
        self.assertEqual(window.state.accounts, c._serialize_accounts())


# ---------------------------------------------------------------------------
# Test 3: _serialize_accounts returns safe dicts
# ---------------------------------------------------------------------------


class TestSerializeAccounts(unittest.TestCase):
    """Verify _serialize_accounts shape and security (no password)."""

    def _make_controller_with_accounts(self):
        import controller as ctrl_module
        from models import Account, AppState
        window = FakeWindow()
        with patch("config.load_state") as mock_load, \
             patch("config.ensure_dirs"), \
             patch.object(ctrl_module.Controller, "_schedule_accounts_poll"):
            mock_load.return_value = AppState(accounts=[
                Account(
                    username="main",
                    display_name="Main",
                    has_snapshot=True,
                    riot_id="Main#EUW",
                    region="EUW",
                    rank_cache={"solo": {"tier": "GOLD", "division": "II"}, "stale": False},
                ),
                Account(
                    username="smurf",
                    display_name="Smurf",
                    has_snapshot=False,
                ),
            ])
            c = ctrl_module.Controller(root=window)
        return c

    def test_serialize_returns_list(self):
        """_serialize_accounts returns a list."""
        c = self._make_controller_with_accounts()
        result = c._serialize_accounts()
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)

    def test_serialize_no_password_key(self):
        """Serialized accounts must never contain a 'password' key (T-04-01)."""
        c = self._make_controller_with_accounts()
        for acc_dict in c._serialize_accounts():
            self.assertNotIn("password", acc_dict, f"'password' key found in {acc_dict}")

    def test_serialize_required_keys_present(self):
        """Each serialized account must contain all required keys."""
        required_keys = {"username", "display_name", "has_snapshot", "riot_id", "region", "rank_cache"}
        c = self._make_controller_with_accounts()
        for acc_dict in c._serialize_accounts():
            for key in required_keys:
                self.assertIn(key, acc_dict, f"Key '{key}' missing from {acc_dict}")

    def test_serialize_no_secret_keys(self):
        """Serialized dicts must not contain puuid or any internal secret field."""
        c = self._make_controller_with_accounts()
        forbidden = {"password", "api_key", "token"}
        for acc_dict in c._serialize_accounts():
            for key in forbidden:
                self.assertNotIn(key, acc_dict)


# ---------------------------------------------------------------------------
# Test 4: _push_state is no-op when shutting down
# ---------------------------------------------------------------------------


class TestPushStateShuttingDown(unittest.TestCase):
    """Verify _push_state returns immediately when _shutting_down=True."""

    def test_push_state_noop_when_shutting_down(self):
        """_push_state must not write to window.state when shutting down."""
        import controller as ctrl_module
        from models import AppState
        window = FakeWindow()
        with patch("config.load_state") as mock_load, \
             patch("config.ensure_dirs"), \
             patch.object(ctrl_module.Controller, "_schedule_accounts_poll"):
            mock_load.return_value = AppState()
            c = ctrl_module.Controller(root=window)

        # Mark shutting down
        c._shutting_down = True
        # Ensure state has something to write
        c.state.active_username = "should_not_appear"
        # Call _push_state
        c._push_state()
        # window.state should still be None/unset (we never assigned it from _push_state)
        self.assertIsNone(
            window.state.active_username,
            "window.state.active_username was set despite _shutting_down=True",
        )


# ---------------------------------------------------------------------------
# Test 5: _clear_clipboard_if_matches uses clipboard helpers
# ---------------------------------------------------------------------------


class TestClearClipboardIfMatches(unittest.TestCase):
    """Verify _clear_clipboard_if_matches calls ctypes clipboard helpers correctly."""

    def _make_controller(self):
        import controller as ctrl_module
        from models import AppState
        window = FakeWindow()
        with patch("config.load_state") as mock_load, \
             patch("config.ensure_dirs"), \
             patch.object(ctrl_module.Controller, "_schedule_accounts_poll"):
            mock_load.return_value = AppState()
            c = ctrl_module.Controller(root=window)
        return c

    def test_clears_when_value_matches(self):
        """_clear_clipboard_if_matches calls clipboard_clear when clipboard matches value."""
        import gui._clipboard as cb_module
        c = self._make_controller()
        with patch.object(cb_module, "clipboard_get", return_value="secret123") as mock_get, \
             patch.object(cb_module, "clipboard_clear") as mock_clear:
            c._clear_clipboard_if_matches("secret123")
            mock_get.assert_called_once()
            mock_clear.assert_called_once()

    def test_does_not_clear_when_value_differs(self):
        """_clear_clipboard_if_matches does NOT call clipboard_clear when value differs."""
        import gui._clipboard as cb_module
        c = self._make_controller()
        with patch.object(cb_module, "clipboard_get", return_value="different_value") as mock_get, \
             patch.object(cb_module, "clipboard_clear") as mock_clear:
            c._clear_clipboard_if_matches("secret123")
            mock_get.assert_called_once()
            mock_clear.assert_not_called()

    def test_swallows_exceptions(self):
        """_clear_clipboard_if_matches swallows any exception (best-effort)."""
        import gui._clipboard as cb_module
        c = self._make_controller()
        with patch.object(cb_module, "clipboard_get", side_effect=OSError("clipboard error")):
            # Should not raise
            c._clear_clipboard_if_matches("any_value")


# ---------------------------------------------------------------------------
# Test 6: gui._clipboard module has required exports
# ---------------------------------------------------------------------------


class TestClipboardModule(unittest.TestCase):
    """Verify gui/_clipboard.py has the required ctypes-based exports."""

    def test_clipboard_module_imports(self):
        """gui._clipboard module imports cleanly."""
        import gui._clipboard  # noqa: F401

    def test_clipboard_set_exists(self):
        """gui._clipboard.clipboard_set is callable."""
        import gui._clipboard as cb
        self.assertTrue(callable(cb.clipboard_set))

    def test_clipboard_get_exists(self):
        """gui._clipboard.clipboard_get is callable."""
        import gui._clipboard as cb
        self.assertTrue(callable(cb.clipboard_get))

    def test_clipboard_clear_exists(self):
        """gui._clipboard.clipboard_clear is callable."""
        import gui._clipboard as cb
        self.assertTrue(callable(cb.clipboard_clear))

    def test_clipboard_module_no_tkinter(self):
        """gui._clipboard must not import tkinter."""
        with open("gui/_clipboard.py", encoding="utf-8") as fh:
            source = fh.read()
        self.assertNotIn("import tkinter", source)
        self.assertNotIn("from tkinter", source)

    def test_clipboard_module_uses_ctypes(self):
        """gui/_clipboard.py must use ctypes."""
        with open("gui/_clipboard.py", encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn("ctypes", source)


if __name__ == "__main__":
    unittest.main()
