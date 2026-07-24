"""Tests for update_checker.py (ONBOARD-03).

Unit tests only — requests.get is always mocked. Never calls the live
GitHub API.
"""
from __future__ import annotations

import pathlib
import time
import unittest
from unittest.mock import MagicMock, patch

import update_checker


class _MockResponse:
    """Reusable mock requests.Response."""

    def __init__(self, status_code: int, json_data=None):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}

    def json(self):
        return self._json_data


class TestParseSemver(unittest.TestCase):
    def test_parses_v_prefixed_tag(self):
        self.assertEqual(update_checker._parse_semver("v2.2.0"), (2, 2, 0))

    def test_parses_bare_tag(self):
        self.assertEqual(update_checker._parse_semver("2.10.3"), (2, 10, 3))

    def test_comparison_respects_numeric_not_lexical_order(self):
        self.assertGreater(update_checker._parse_semver("2.10.0"), update_checker._parse_semver("2.9.0"))


class TestCheckForUpdateThrottle(unittest.TestCase):
    def test_ttl_not_elapsed_returns_none_without_network_call(self):
        with patch("update_checker.requests.get") as mock_get:
            result = update_checker.check_for_update("2.1.0", time.time())
        self.assertIsNone(result)
        mock_get.assert_not_called()

    def test_never_checked_before_elapses_ttl(self):
        """last_checked_at=0.0 (never checked) must NOT be throttled."""
        with patch("update_checker.requests.get") as mock_get:
            mock_get.return_value = _MockResponse(200, {"tag_name": "v2.1.0", "html_url": "https://x"})
            update_checker.check_for_update("2.1.0", 0.0)
        mock_get.assert_called_once()


class TestCheckForUpdateResult(unittest.TestCase):
    def _elapsed_ts(self) -> float:
        return time.time() - update_checker.CHECK_TTL_S - 1

    def test_returns_update_dict_when_newer_tag_exists(self):
        with patch("update_checker.requests.get") as mock_get:
            mock_get.return_value = _MockResponse(
                200, {"tag_name": "v2.2.0", "html_url": "https://github.com/x/releases/v2.2.0"}
            )
            result = update_checker.check_for_update("2.1.0", self._elapsed_ts())
        self.assertEqual(
            result, {"tag_name": "v2.2.0", "html_url": "https://github.com/x/releases/v2.2.0"}
        )

    def test_returns_none_when_tag_equals_current(self):
        with patch("update_checker.requests.get") as mock_get:
            mock_get.return_value = _MockResponse(200, {"tag_name": "v2.1.0", "html_url": "https://x"})
            result = update_checker.check_for_update("2.1.0", self._elapsed_ts())
        self.assertIsNone(result)

    def test_returns_none_when_tag_older_than_current(self):
        with patch("update_checker.requests.get") as mock_get:
            mock_get.return_value = _MockResponse(200, {"tag_name": "v2.0.0", "html_url": "https://x"})
            result = update_checker.check_for_update("2.1.0", self._elapsed_ts())
        self.assertIsNone(result)

    def test_malformed_tag_does_not_raise(self):
        with patch("update_checker.requests.get") as mock_get:
            mock_get.return_value = _MockResponse(200, {"tag_name": "not-a-version", "html_url": "https://x"})
            result = update_checker.check_for_update("2.1.0", self._elapsed_ts())
        self.assertIsNone(result)

    def test_empty_tag_name_returns_none(self):
        with patch("update_checker.requests.get") as mock_get:
            mock_get.return_value = _MockResponse(200, {"tag_name": "", "html_url": "https://x"})
            result = update_checker.check_for_update("2.1.0", self._elapsed_ts())
        self.assertIsNone(result)


class TestCheckForUpdateFailureModes(unittest.TestCase):
    def _elapsed_ts(self) -> float:
        return time.time() - update_checker.CHECK_TTL_S - 1

    def test_connection_error_returns_none(self):
        import requests.exceptions

        with patch("update_checker.requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.ConnectionError("boom")
            result = update_checker.check_for_update("2.1.0", self._elapsed_ts())
        self.assertIsNone(result)

    def test_timeout_returns_none(self):
        import requests.exceptions

        with patch("update_checker.requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.Timeout("boom")
            result = update_checker.check_for_update("2.1.0", self._elapsed_ts())
        self.assertIsNone(result)

    def test_rate_limit_403_returns_none(self):
        with patch("update_checker.requests.get") as mock_get:
            mock_get.return_value = _MockResponse(403)
            result = update_checker.check_for_update("2.1.0", self._elapsed_ts())
        self.assertIsNone(result)

    def test_server_error_500_returns_none(self):
        with patch("update_checker.requests.get") as mock_get:
            mock_get.return_value = _MockResponse(500)
            result = update_checker.check_for_update("2.1.0", self._elapsed_ts())
        self.assertIsNone(result)

    def test_no_authorization_header_ever_sent(self):
        """T-08-05: never ship an Authorization header (no token in a public client)."""
        with patch("update_checker.requests.get") as mock_get:
            mock_get.return_value = _MockResponse(200, {"tag_name": "v2.1.0", "html_url": "https://x"})
            update_checker.check_for_update("2.1.0", self._elapsed_ts())
        _args, kwargs = mock_get.call_args
        self.assertNotIn("headers", kwargs)


# ---------------------------------------------------------------------------
# Controller integration tests (Plan 08-06, ONBOARD-03, D-13/D-14)
#
# credential_store's module-level `keyring` name is bound at whatever module
# first imports it in this process. When a sibling test file (e.g.
# test_account_mgmt.py / test_rank_flow.py) is collected before this one (the
# common full-suite case), credential_store is ALREADY safely bound to that
# file's fake keyring by the time we get here — reloading it again here would
# silently rebind it for every already-collected file for the rest of the
# session (all test modules share one process), desyncing their own
# fake-keyring instances from the one credential_store actually calls (the
# cross-file hazard documented in test_settings.py's _make_controller_ctx).
# But when THIS file happens to be collected FIRST (e.g. an explicit subset
# run such as `pytest tests/test_update_checker.py tests/test_settings.py`,
# collected in CLI order), nobody has installed a fake yet and
# credential_store would otherwise bind to the REAL Windows keyring package —
# never acceptable in this suite. Detect that case (the real package exposes
# `get_keyring`; no fake in this suite does) and only then install our own
# fake + reload, so this file is hermetic in isolation AND never disturbs an
# already-installed sibling fake.
# ---------------------------------------------------------------------------

import config  # noqa: E402
import credential_store  # noqa: E402
import controller as controller_module  # noqa: E402

if hasattr(getattr(credential_store, "keyring", None), "get_keyring"):
    import importlib
    import sys
    import types

    class _FakeKeyringErrors:
        class PasswordDeleteError(Exception):
            pass

    class _FakeKeyring:
        def __init__(self):
            self._store: dict[tuple[str, str], str] = {}
            self.errors = _FakeKeyringErrors()

        def set_password(self, service, username, password):
            self._store[(service, username)] = password

        def get_password(self, service, username):
            return self._store.get((service, username))

        def delete_password(self, service, username):
            key = (service, username)
            if key not in self._store:
                raise _FakeKeyringErrors.PasswordDeleteError(f"{service}/{username} not found")
            del self._store[key]

    _fake_keyring = _FakeKeyring()
    _keyring_mod = types.ModuleType("keyring")
    _keyring_mod.set_password = _fake_keyring.set_password
    _keyring_mod.get_password = _fake_keyring.get_password
    _keyring_mod.delete_password = _fake_keyring.delete_password
    _keyring_errors_mod = types.ModuleType("keyring.errors")
    _keyring_errors_mod.PasswordDeleteError = _FakeKeyringErrors.PasswordDeleteError
    _keyring_mod.errors = _keyring_errors_mod
    sys.modules["keyring"] = _keyring_mod
    sys.modules["keyring.errors"] = _keyring_errors_mod
    importlib.reload(credential_store)


class _FakeWindowState:
    """Attribute sink for window.state.* writes — accepts any attribute."""


class _FakeWindow:
    def __init__(self) -> None:
        self.state = _FakeWindowState()


class TestControllerUpdateCheck(unittest.TestCase):
    """Controller-level integration tests for start_update_check/dismiss_update/
    set_update_check (Plan 08-06). ``update_checker.check_for_update`` is always
    mocked — never a real network call."""

    def _make_controller(self, tmp_path: pathlib.Path):
        fake_window = _FakeWindow()
        patcher_app = patch.object(config, "APP_DIR", tmp_path)
        patcher_json = patch.object(config, "ACCOUNTS_JSON", tmp_path / "accounts.json")
        patcher_sessions = patch.object(config, "SESSIONS_DIR", tmp_path / "sessions")
        patcher_app.start()
        patcher_json.start()
        patcher_sessions.start()
        config.ensure_dirs()
        credential_store.delete_api_key()
        credential_store.delete_api_key_file()
        ctrl = controller_module.Controller(fake_window)
        ctrl._fake_window = fake_window
        self._ctrls.append(ctrl)
        return ctrl, [patcher_app, patcher_json, patcher_sessions]

    def setUp(self) -> None:
        self._ctrls = []

    def tearDown(self) -> None:
        for ctrl in self._ctrls:
            ctrl.shutdown()

    def test_disabled_does_not_spawn_check_thread(self) -> None:
        """update_check_enabled=False -> start_update_check() spawns no thread
        and never touches check_for_update; no pill state is set."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, patchers = self._make_controller(pathlib.Path(tmp))
            try:
                ctrl.state.update_check_enabled = False
                with patch("threading.Thread") as mock_thread_cls:
                    ctrl.start_update_check()
                    mock_thread_cls.assert_not_called()
                self.assertFalse(ctrl._update_available)
                self.assertIsNone(ctrl._update_tag)
            finally:
                for p in patchers:
                    p.stop()

    def test_enabled_spawns_daemon_thread(self) -> None:
        """update_check_enabled=True -> start_update_check() spawns exactly one
        daemon thread targeting _run_update_check."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, patchers = self._make_controller(pathlib.Path(tmp))
            try:
                ctrl.state.update_check_enabled = True
                with patch("threading.Thread") as mock_thread_cls:
                    mock_t = MagicMock()
                    mock_thread_cls.return_value = mock_t
                    ctrl.start_update_check()
                    mock_thread_cls.assert_called_once()
                    _args, kwargs = mock_thread_cls.call_args
                    self.assertEqual(kwargs.get("target"), ctrl._run_update_check)
                    self.assertTrue(kwargs.get("daemon"))
                    mock_t.start.assert_called_once()
            finally:
                for p in patchers:
                    p.stop()

    def test_newer_non_dismissed_result_sets_pill_state(self) -> None:
        """A mocked newer release (not the dismissed tag) sets update_available/
        update_tag/update_url and pushes them to window.state."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, patchers = self._make_controller(pathlib.Path(tmp))
            try:
                ctrl.state.dismissed_update_version = None
                ctrl.state.update_last_checked = 0.0
                with patch(
                    "update_checker.check_for_update",
                    return_value={
                        "tag_name": "v2.2.0",
                        "html_url": "https://github.com/x/releases/v2.2.0",
                    },
                ):
                    ctrl._run_update_check()

                self.assertTrue(ctrl._update_available)
                self.assertEqual(ctrl._update_tag, "v2.2.0")
                self.assertEqual(
                    ctrl._update_url, "https://github.com/x/releases/v2.2.0"
                )
                self.assertTrue(ctrl._fake_window.state.update_available)
                self.assertEqual(ctrl._fake_window.state.update_tag, "v2.2.0")
            finally:
                for p in patchers:
                    p.stop()

    def test_result_matching_dismissed_version_stays_hidden(self) -> None:
        """A mocked result whose tag equals dismissed_update_version never sets
        update_available (D-14)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, patchers = self._make_controller(pathlib.Path(tmp))
            try:
                ctrl.state.dismissed_update_version = "v2.2.0"
                ctrl.state.update_last_checked = 0.0
                with patch(
                    "update_checker.check_for_update",
                    return_value={
                        "tag_name": "v2.2.0",
                        "html_url": "https://github.com/x/releases/v2.2.0",
                    },
                ):
                    ctrl._run_update_check()

                self.assertFalse(ctrl._update_available)
                self.assertIsNone(ctrl._update_tag)
            finally:
                for p in patchers:
                    p.stop()

    def test_no_result_leaves_pill_hidden(self) -> None:
        """check_for_update returning None (silent failure/no-update) leaves
        the pill hidden."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, patchers = self._make_controller(pathlib.Path(tmp))
            try:
                ctrl.state.update_last_checked = 0.0
                with patch("update_checker.check_for_update", return_value=None):
                    ctrl._run_update_check()
                self.assertFalse(ctrl._update_available)
            finally:
                for p in patchers:
                    p.stop()

    def test_run_update_check_advances_throttle_marker_when_due(self) -> None:
        """_run_update_check persists a fresh update_last_checked when the TTL
        window had elapsed (Pitfall 4 — app-level TTL is the real throttle)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, patchers = self._make_controller(pathlib.Path(tmp))
            try:
                ctrl.state.update_last_checked = 0.0
                with patch("update_checker.check_for_update", return_value=None):
                    ctrl._run_update_check()
                self.assertGreater(ctrl.state.update_last_checked, 0.0)
                reloaded = config.load_state()
                self.assertGreater(reloaded.update_last_checked, 0.0)
            finally:
                for p in patchers:
                    p.stop()

    def test_dismiss_update_persists_and_clears_pill(self) -> None:
        """dismiss_update(version) persists dismissed_update_version (config
        round-trip) and clears the pill."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, patchers = self._make_controller(pathlib.Path(tmp))
            try:
                ctrl._update_available = True
                ctrl._update_tag = "v2.2.0"
                ctrl.dismiss_update("v2.2.0")

                self.assertFalse(ctrl._update_available)
                self.assertEqual(ctrl.state.dismissed_update_version, "v2.2.0")
                reloaded = config.load_state()
                self.assertEqual(reloaded.dismissed_update_version, "v2.2.0")
                self.assertFalse(ctrl._fake_window.state.update_available)
            finally:
                for p in patchers:
                    p.stop()

    def test_set_update_check_false_persists_and_clears_pill(self) -> None:
        """set_update_check(False) persists update_check_enabled=False and
        clears any shown pill."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, patchers = self._make_controller(pathlib.Path(tmp))
            try:
                ctrl._update_available = True
                ctrl.set_update_check(False)

                self.assertFalse(ctrl.state.update_check_enabled)
                self.assertFalse(ctrl._update_available)
                reloaded = config.load_state()
                self.assertFalse(reloaded.update_check_enabled)
            finally:
                for p in patchers:
                    p.stop()

    def test_set_update_check_true_persists(self) -> None:
        """set_update_check(True) persists update_check_enabled=True."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, patchers = self._make_controller(pathlib.Path(tmp))
            try:
                ctrl.set_update_check(False)
                ctrl.set_update_check(True)

                self.assertTrue(ctrl.state.update_check_enabled)
                reloaded = config.load_state()
                self.assertTrue(reloaded.update_check_enabled)
            finally:
                for p in patchers:
                    p.stop()


if __name__ == "__main__":
    unittest.main()
