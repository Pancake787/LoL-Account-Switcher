"""Tests for Controller STATUS-01 wiring — update_client_status() + poller teardown.

New in Plan 05-02: verifies the StatusPoller.on_change -> Controller.update_client_status
-> window.state.client_running/game_live path (D-16/D-17), and that shutdown() stops
a wired _status_poller centrally (WR-03).

Uses the same fake-keyring-injection + tmp-config-redirection pattern as
test_account_mgmt.py so Controller() can be constructed without touching the
real Windows Credential Manager or accounts.json.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Fake keyring module — backed by a simple dict, never touches WCM
# ---------------------------------------------------------------------------

class _FakeKeyringErrors:
    """Minimal stub of keyring.errors."""
    class PasswordDeleteError(Exception):
        pass


class _FakeKeyring:
    """In-memory keyring backend."""

    def __init__(self):
        self._store: dict[tuple[str, str], str] = {}
        self.errors = _FakeKeyringErrors()

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        key = (service, username)
        if key not in self._store:
            raise _FakeKeyringErrors.PasswordDeleteError(f"{service}/{username} not found")
        del self._store[(service, username)]

    def reset(self) -> None:
        self._store.clear()


_fake_keyring = _FakeKeyring()

if "keyring" not in sys.modules:
    _keyring_mod = types.ModuleType("keyring")
    _keyring_mod.set_password = _fake_keyring.set_password
    _keyring_mod.get_password = _fake_keyring.get_password
    _keyring_mod.delete_password = _fake_keyring.delete_password
    _keyring_errors_mod = types.ModuleType("keyring.errors")
    _keyring_errors_mod.PasswordDeleteError = _FakeKeyringErrors.PasswordDeleteError
    _keyring_mod.errors = _keyring_errors_mod
    sys.modules["keyring"] = _keyring_mod
    sys.modules["keyring.errors"] = _keyring_errors_mod


# ---------------------------------------------------------------------------
# Fake pywebview window — has a .state object for window.state.* writes
# ---------------------------------------------------------------------------

class _FakeWindowState:
    """Minimal pywebview window.state stub for tests."""
    accounts = None
    active_username = None
    status = None
    status_message = None
    pending_first_login = None
    client_running = None
    game_live = None


class _FakeWindow:
    """Fake pywebview Window object — has a .state attribute only."""

    def __init__(self):
        self.state = _FakeWindowState()


class _FakePoller:
    """Fake StatusPoller — records whether stop() was called."""

    def __init__(self):
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


import config  # noqa: E402
import controller as controller_module  # noqa: E402


class TestUpdateClientStatus(unittest.TestCase):
    """Controller.update_client_status() sets attributes and pushes to window.state."""

    def setUp(self):
        _fake_keyring.reset()

    def _make_controller(self, tmp_path: pathlib.Path):
        patcher_app = patch.object(config, "APP_DIR", tmp_path)
        patcher_json = patch.object(config, "ACCOUNTS_JSON", tmp_path / "accounts.json")
        patcher_sessions = patch.object(config, "SESSIONS_DIR", tmp_path / "sessions")
        patcher_app.start()
        patcher_json.start()
        patcher_sessions.start()
        config.ensure_dirs()
        fake_window = _FakeWindow()
        ctrl = controller_module.Controller(fake_window)
        return ctrl, fake_window, [patcher_app, patcher_json, patcher_sessions]

    def test_init_defaults_both_false(self):
        """__init__ initializes _client_running and _game_live to False."""
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, _window, patchers = self._make_controller(pathlib.Path(tmp))
            try:
                self.assertFalse(ctrl._client_running)
                self.assertFalse(ctrl._game_live)
            finally:
                for p in patchers:
                    p.stop()

    def test_update_client_status_sets_attributes_and_pushes(self):
        """update_client_status(True, False) sets window.state.client_running/game_live."""
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, window, patchers = self._make_controller(pathlib.Path(tmp))
            try:
                ctrl.update_client_status(True, False)
                self.assertTrue(ctrl._client_running)
                self.assertFalse(ctrl._game_live)
                self.assertIs(window.state.client_running, True)
                self.assertIs(window.state.game_live, False)
            finally:
                for p in patchers:
                    p.stop()

    def test_update_client_status_game_live_transition(self):
        """A second call with (True, True) updates both fields."""
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, window, patchers = self._make_controller(pathlib.Path(tmp))
            try:
                ctrl.update_client_status(True, False)
                ctrl.update_client_status(True, True)
                self.assertTrue(ctrl._game_live)
                self.assertIs(window.state.game_live, True)
            finally:
                for p in patchers:
                    p.stop()

    def test_shutdown_stops_wired_status_poller(self):
        """shutdown() calls stop() on a wired _status_poller (WR-03 centralized teardown)."""
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, _window, patchers = self._make_controller(pathlib.Path(tmp))
            try:
                fake_poller = _FakePoller()
                ctrl._status_poller = fake_poller
                ctrl.shutdown()
                self.assertTrue(fake_poller.stopped)
            finally:
                for p in patchers:
                    p.stop()

    def test_shutdown_safe_when_no_poller_wired(self):
        """shutdown() does not raise when _status_poller was never set (e.g. headless/tests)."""
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, _window, patchers = self._make_controller(pathlib.Path(tmp))
            try:
                self.assertIsNone(ctrl._status_poller)
                ctrl.shutdown()  # must not raise
            finally:
                for p in patchers:
                    p.stop()


if __name__ == "__main__":
    unittest.main()
