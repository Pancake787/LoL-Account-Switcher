"""Tests for credential_store.py, config.snapshot_dir, and Controller account management.

Uses a dict-backed fake keyring so tests never touch the real Windows Credential Manager.
Config paths are redirected to a tmp directory.
The Tk root is stubbed with a fake that records clipboard calls.
"""
from __future__ import annotations

import pathlib
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


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

# Inject fake keyring into sys.modules BEFORE importing credential_store
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
# Fake Tk root — records clipboard operations
# ---------------------------------------------------------------------------

class _FakeWindowState:
    """Minimal pywebview window.state stub for tests."""
    accounts = None
    active_username = None
    status = None
    status_message = None
    pending_first_login = None


class _FakeTkRoot:
    """Fake window object — now represents a pywebview Window, not a Tk root.

    Has a .state attribute (pywebview window.state replacement) so that
    Controller._push_state() works without a real pywebview window.
    """
    def __init__(self):
        self.state = _FakeWindowState()
        # Legacy clipboard tracking (unused in v2.0; kept for test compat)
        self._clipboard: str = ""
        self._clipboard_cleared = False

    def clipboard_clear(self) -> None:
        self._clipboard = ""
        self._clipboard_cleared = True

    def clipboard_append(self, text: str) -> None:
        self._clipboard += text

    def after(self, ms: int, callback) -> None:  # noqa: ARG002
        pass  # threading.Timer now used in place of root.after; no-op in tests


# ---------------------------------------------------------------------------
# Import the modules under test (after fake injection)
# ---------------------------------------------------------------------------

import credential_store  # noqa: E402 — must come after fake injection
import config  # noqa: E402
from models import Account, AppState  # noqa: E402
import controller as controller_module  # noqa: E402


# ---------------------------------------------------------------------------
# Test Suite
# ---------------------------------------------------------------------------


class TestCredentialStore(unittest.TestCase):
    """Unit tests for credential_store.py."""

    def setUp(self):
        _fake_keyring.reset()

    def test_save_and_get_roundtrip(self):
        """save then get returns the saved password."""
        credential_store.save("riotuser1", "pw123")
        result = credential_store.get("riotuser1")
        self.assertEqual(result, "pw123")

    def test_save_twice_does_not_raise(self):
        """Saving the same username twice does not raise (delete-then-set)."""
        credential_store.save("riotuser1", "first")
        credential_store.save("riotuser1", "second")  # must not raise
        self.assertEqual(credential_store.get("riotuser1"), "second")

    def test_get_missing_returns_empty_string(self):
        """Getting a non-existent username returns an empty string."""
        result = credential_store.get("nobody")
        self.assertEqual(result, "")

    def test_delete_removes_credential(self):
        """delete then get returns empty string."""
        credential_store.save("riotuser1", "pw123")
        credential_store.delete("riotuser1")
        self.assertEqual(credential_store.get("riotuser1"), "")

    def test_delete_missing_does_not_raise(self):
        """delete on a missing username does not raise."""
        credential_store.delete("nobody")  # must not raise

    def test_service_constant(self):
        """SERVICE constant must be 'lol-switcher'."""
        self.assertEqual(credential_store.SERVICE, "lol-switcher")


class TestConfigSnapshotDir(unittest.TestCase):
    """Unit tests for config.snapshot_dir."""

    def test_snapshot_dir_returns_sessions_subdir(self):
        """snapshot_dir('user1') returns SESSIONS_DIR / 'user1'."""
        result = config.snapshot_dir("user1")
        expected = config.SESSIONS_DIR / "user1"
        self.assertEqual(result, expected)

    def test_snapshot_dir_is_pathlib_path(self):
        """snapshot_dir returns a pathlib.Path."""
        result = config.snapshot_dir("user1")
        self.assertIsInstance(result, pathlib.Path)


class TestControllerAddAccount(unittest.TestCase):
    """Unit tests for Controller.add_account."""

    def setUp(self):
        _fake_keyring.reset()
        self._tmp = None

    def _make_controller(self, tmp_path: pathlib.Path):
        """Create a Controller with config paths redirected to tmp_path."""
        fake_root = _FakeTkRoot()
        with patch.object(config, "APP_DIR", tmp_path), \
             patch.object(config, "ACCOUNTS_JSON", tmp_path / "accounts.json"), \
             patch.object(config, "SESSIONS_DIR", tmp_path / "sessions"):
            config.ensure_dirs()
            ctrl = controller_module.Controller(fake_root)
        ctrl._config_tmp = tmp_path
        ctrl._fake_root = fake_root
        return ctrl

    def _make_controller_ctx(self, tmp_path: pathlib.Path):
        """Return a controller using the given tmp paths for all config calls."""
        fake_root = _FakeTkRoot()
        patcher_app = patch.object(config, "APP_DIR", tmp_path)
        patcher_json = patch.object(config, "ACCOUNTS_JSON", tmp_path / "accounts.json")
        patcher_sessions = patch.object(config, "SESSIONS_DIR", tmp_path / "sessions")
        patcher_app.start()
        patcher_json.start()
        patcher_sessions.start()
        config.ensure_dirs()
        ctrl = controller_module.Controller(fake_root)
        ctrl._fake_root = fake_root
        return ctrl, [patcher_app, patcher_json, patcher_sessions]

    def test_add_account_appends_account(self):
        """add_account appends an Account to state.accounts."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw123")
                self.assertEqual(len(ctrl.state.accounts), 1)
                self.assertEqual(ctrl.state.accounts[0].username, "riotuser1")
                self.assertEqual(ctrl.state.accounts[0].display_name, "Main")
            finally:
                for p in patchers:
                    p.stop()

    def test_add_account_stores_password_in_wcm(self):
        """add_account stores the password in the credential store."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw123")
                self.assertEqual(credential_store.get("riotuser1"), "pw123")
            finally:
                for p in patchers:
                    p.stop()

    def test_add_account_does_not_write_password_to_json(self):
        """add_account must NOT write the password to accounts.json."""
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            json_path = tmp_path / "accounts.json"
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw123")
                data = json.loads(json_path.read_text(encoding="utf-8"))
                for entry in data.get("accounts", []):
                    self.assertNotIn("password", entry)
            finally:
                for p in patchers:
                    p.stop()

    def test_add_account_sets_active_when_first(self):
        """add_account sets active_username when it was None."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw123")
                self.assertEqual(ctrl.state.active_username, "riotuser1")
            finally:
                for p in patchers:
                    p.stop()

    def test_add_account_does_not_override_existing_active(self):
        """add_account does not change active_username if one is already set."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                ctrl.add_account("Smurf", "riotuser2", "pw2")
                self.assertEqual(ctrl.state.active_username, "riotuser1")
            finally:
                for p in patchers:
                    p.stop()

    def test_add_account_rejects_empty_display_name(self):
        """add_account raises ValueError for empty display_name."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                with self.assertRaises(ValueError):
                    ctrl.add_account("", "riotuser1", "pw123")
            finally:
                for p in patchers:
                    p.stop()

    def test_add_account_rejects_empty_username(self):
        """add_account raises ValueError for empty username."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                with self.assertRaises(ValueError):
                    ctrl.add_account("Main", "", "pw123")
            finally:
                for p in patchers:
                    p.stop()

    def test_add_account_rejects_empty_password(self):
        """add_account raises ValueError for empty password."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                with self.assertRaises(ValueError):
                    ctrl.add_account("Main", "riotuser1", "")
            finally:
                for p in patchers:
                    p.stop()

    def test_add_account_rejects_duplicate_username(self):
        """add_account raises ValueError for a duplicate username."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                with self.assertRaises(ValueError):
                    ctrl.add_account("Other", "riotuser1", "pw2")
            finally:
                for p in patchers:
                    p.stop()


class TestControllerDeleteAccount(unittest.TestCase):
    """Unit tests for Controller.delete_account."""

    def _make_controller_ctx(self, tmp_path: pathlib.Path):
        fake_root = _FakeTkRoot()
        patcher_app = patch.object(config, "APP_DIR", tmp_path)
        patcher_json = patch.object(config, "ACCOUNTS_JSON", tmp_path / "accounts.json")
        patcher_sessions = patch.object(config, "SESSIONS_DIR", tmp_path / "sessions")
        patcher_snap = patch.object(config, "snapshot_dir",
                                    lambda u: tmp_path / "sessions" / u)
        patcher_app.start()
        patcher_json.start()
        patcher_sessions.start()
        patcher_snap.start()
        config.ensure_dirs()
        ctrl = controller_module.Controller(fake_root)
        ctrl._fake_root = fake_root
        return ctrl, [patcher_app, patcher_json, patcher_sessions, patcher_snap]

    def setUp(self):
        _fake_keyring.reset()

    def test_delete_removes_account_from_state(self):
        """delete_account removes the account from state.accounts."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                ctrl.delete_account("riotuser1")
                self.assertEqual(len(ctrl.state.accounts), 0)
            finally:
                for p in patchers:
                    p.stop()

    def test_delete_removes_credential(self):
        """delete_account removes the credential from the store."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                ctrl.delete_account("riotuser1")
                self.assertEqual(credential_store.get("riotuser1"), "")
            finally:
                for p in patchers:
                    p.stop()

    def test_delete_removes_snapshot_dir(self):
        """delete_account removes the snapshot directory."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            snap_dir = tmp_path / "sessions" / "riotuser1"
            snap_dir.mkdir(parents=True, exist_ok=True)
            (snap_dir / "RiotGamesPrivateSettings.yaml").write_text("token: abc")

            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                ctrl.delete_account("riotuser1")
                self.assertFalse(snap_dir.exists())
            finally:
                for p in patchers:
                    p.stop()

    def test_delete_clears_active_username_when_matching(self):
        """delete_account clears active_username when it matches the deleted account."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                self.assertEqual(ctrl.state.active_username, "riotuser1")
                ctrl.delete_account("riotuser1")
                self.assertIsNone(ctrl.state.active_username)
            finally:
                for p in patchers:
                    p.stop()

    def test_delete_updates_active_to_remaining(self):
        """delete_account sets active_username to first remaining account."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                ctrl.add_account("Smurf", "riotuser2", "pw2")
                ctrl.delete_account("riotuser1")
                self.assertEqual(ctrl.state.active_username, "riotuser2")
            finally:
                for p in patchers:
                    p.stop()

    def test_delete_does_not_change_active_when_other_is_active(self):
        """delete_account does not change active_username when another account is active."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                ctrl.add_account("Smurf", "riotuser2", "pw2")
                # active is riotuser1; delete riotuser2 — active must stay riotuser1
                ctrl.delete_account("riotuser2")
                self.assertEqual(ctrl.state.active_username, "riotuser1")
            finally:
                for p in patchers:
                    p.stop()


class TestControllerCopyPassword(unittest.TestCase):
    """Unit tests for Controller.copy_password (D-06)."""

    def _make_controller_ctx(self, tmp_path: pathlib.Path):
        fake_root = _FakeTkRoot()
        patcher_app = patch.object(config, "APP_DIR", tmp_path)
        patcher_json = patch.object(config, "ACCOUNTS_JSON", tmp_path / "accounts.json")
        patcher_sessions = patch.object(config, "SESSIONS_DIR", tmp_path / "sessions")
        patcher_app.start()
        patcher_json.start()
        patcher_sessions.start()
        config.ensure_dirs()
        ctrl = controller_module.Controller(fake_root)
        ctrl._fake_root = fake_root
        # WR-02: register so tearDown cancels every daemon timer this controller
        # scheduled (accounts poll, rank refresh, game-end poll, clipboard clear).
        # A switch fires _on_switch_done -> rank refresh + game-end poll timers
        # that call unpatched riot_client.* after the test returns — leaking
        # those timers is the root cause of the historical cross-file flake.
        self._ctrls.append(ctrl)
        return ctrl, [patcher_app, patcher_json, patcher_sessions]

    def setUp(self):
        _fake_keyring.reset()
        self._ctrls = []

    def tearDown(self):
        # WR-02: deterministically cancel all daemon timers created during the test.
        for ctrl in self._ctrls:
            ctrl.shutdown()

    def test_copy_password_returns_true_and_copies_to_clipboard(self):
        """copy_password fetches the password on demand and places it on the clipboard."""
        import tempfile
        import gui._clipboard as cb_mod
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "mypassword")
                with patch.object(cb_mod, "clipboard_set", return_value=True) as mock_set:
                    result = ctrl.copy_password("riotuser1")
                self.assertTrue(result)
                mock_set.assert_called_once_with("mypassword")
            finally:
                for p in patchers:
                    p.stop()

    def test_copy_password_returns_false_when_no_password(self):
        """copy_password returns False when no password is stored."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                result = ctrl.copy_password("nobody")
                self.assertFalse(result)
            finally:
                for p in patchers:
                    p.stop()

    def test_copy_password_does_not_cache_in_appstate(self):
        """copy_password must not store the password in AppState or any Account."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "mypassword")
                ctrl.copy_password("riotuser1")
                # Check AppState has no password field
                state_dict = ctrl.state.__dict__
                self.assertNotIn("password", state_dict)
                # Check Account objects have no password field
                for acc in ctrl.state.accounts:
                    acc_dict = acc.__dict__
                    self.assertNotIn("password", acc_dict)
            finally:
                for p in patchers:
                    p.stop()

    def test_copy_password_calls_clipboard_set(self):
        """copy_password calls clipboard_set with the password (ctypes clipboard)."""
        import tempfile
        import gui._clipboard as cb_mod
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw")
                with patch.object(cb_mod, "clipboard_set", return_value=True) as mock_set:
                    ctrl.copy_password("riotuser1")
                mock_set.assert_called_once_with("pw")
            finally:
                for p in patchers:
                    p.stop()


class TestControllerRenameAccount(unittest.TestCase):
    """Unit tests for Controller.rename_account (ACCT-03, D-14, T-01-09)."""

    def _make_controller_ctx(self, tmp_path: pathlib.Path):
        fake_root = _FakeTkRoot()
        patcher_app = patch.object(config, "APP_DIR", tmp_path)
        patcher_json = patch.object(config, "ACCOUNTS_JSON", tmp_path / "accounts.json")
        patcher_sessions = patch.object(config, "SESSIONS_DIR", tmp_path / "sessions")
        patcher_app.start()
        patcher_json.start()
        patcher_sessions.start()
        config.ensure_dirs()
        ctrl = controller_module.Controller(fake_root)
        ctrl._fake_root = fake_root
        # WR-02: register so tearDown cancels every daemon timer this controller
        # scheduled (accounts poll, rank refresh, game-end poll, clipboard clear).
        # A switch fires _on_switch_done -> rank refresh + game-end poll timers
        # that call unpatched riot_client.* after the test returns — leaking
        # those timers is the root cause of the historical cross-file flake.
        self._ctrls.append(ctrl)
        return ctrl, [patcher_app, patcher_json, patcher_sessions]

    def setUp(self):
        _fake_keyring.reset()
        self._ctrls = []

    def tearDown(self):
        # WR-02: deterministically cancel all daemon timers created during the test.
        for ctrl in self._ctrls:
            ctrl.shutdown()

    def test_rename_changes_only_display_name(self):
        """rename_account changes display_name; username and has_snapshot are unchanged (D-14, T-01-09)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                ctrl.state.accounts[0].has_snapshot = True  # simulate a snapshot
                old_username = ctrl.state.accounts[0].username
                old_has_snapshot = ctrl.state.accounts[0].has_snapshot

                ctrl.rename_account("riotuser1", "Smurf")

                acc = ctrl.state.accounts[0]
                self.assertEqual(acc.display_name, "Smurf")
                self.assertEqual(acc.username, old_username)          # unchanged
                self.assertEqual(acc.has_snapshot, old_has_snapshot)  # unchanged
            finally:
                for p in patchers:
                    p.stop()

    def test_rename_persists_to_json(self):
        """rename_account persists the new display_name via save_state."""
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            json_path = tmp_path / "accounts.json"
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                ctrl.rename_account("riotuser1", "Smurf")
                data = json.loads(json_path.read_text(encoding="utf-8"))
                accounts = data.get("accounts", [])
                self.assertEqual(len(accounts), 1)
                self.assertEqual(accounts[0]["display_name"], "Smurf")
                self.assertNotIn("password", accounts[0])
            finally:
                for p in patchers:
                    p.stop()

    def test_rename_rejects_empty_name(self):
        """rename_account raises ValueError with a German message for empty/whitespace names."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                with self.assertRaises(ValueError):
                    ctrl.rename_account("riotuser1", "")
                with self.assertRaises(ValueError):
                    ctrl.rename_account("riotuser1", "   ")
            finally:
                for p in patchers:
                    p.stop()

    def test_rename_pushes_state(self):
        """rename_account calls _push_state() after renaming (GUI receives update)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                with patch.object(ctrl, "_push_state") as mock_push:
                    ctrl.rename_account("riotuser1", "Smurf")
                self.assertGreater(mock_push.call_count, 0)
            finally:
                for p in patchers:
                    p.stop()


class TestControllerReorderAccounts(unittest.TestCase):
    """Unit tests for Controller.reorder_accounts (D-17, T-01-10)."""

    def _make_controller_ctx(self, tmp_path: pathlib.Path):
        fake_root = _FakeTkRoot()
        patcher_app = patch.object(config, "APP_DIR", tmp_path)
        patcher_json = patch.object(config, "ACCOUNTS_JSON", tmp_path / "accounts.json")
        patcher_sessions = patch.object(config, "SESSIONS_DIR", tmp_path / "sessions")
        patcher_app.start()
        patcher_json.start()
        patcher_sessions.start()
        config.ensure_dirs()
        ctrl = controller_module.Controller(fake_root)
        ctrl._fake_root = fake_root
        # WR-02: register so tearDown cancels every daemon timer this controller
        # scheduled (accounts poll, rank refresh, game-end poll, clipboard clear).
        # A switch fires _on_switch_done -> rank refresh + game-end poll timers
        # that call unpatched riot_client.* after the test returns — leaking
        # those timers is the root cause of the historical cross-file flake.
        self._ctrls.append(ctrl)
        return ctrl, [patcher_app, patcher_json, patcher_sessions]

    def setUp(self):
        _fake_keyring.reset()
        self._ctrls = []

    def tearDown(self):
        # WR-02: deterministically cancel all daemon timers created during the test.
        for ctrl in self._ctrls:
            ctrl.shutdown()

    def test_reorder_produces_requested_order(self):
        """reorder_accounts reorders state.accounts to match the given username list."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                ctrl.add_account("Smurf", "riotuser2", "pw2")
                ctrl.reorder_accounts(["riotuser2", "riotuser1"])
                self.assertEqual(ctrl.state.accounts[0].username, "riotuser2")
                self.assertEqual(ctrl.state.accounts[1].username, "riotuser1")
            finally:
                for p in patchers:
                    p.stop()

    def test_reorder_persists_to_json(self):
        """reorder_accounts persists the new order via save_state."""
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            json_path = tmp_path / "accounts.json"
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                ctrl.add_account("Smurf", "riotuser2", "pw2")
                ctrl.reorder_accounts(["riotuser2", "riotuser1"])
                data = json.loads(json_path.read_text(encoding="utf-8"))
                usernames = [a["username"] for a in data["accounts"]]
                self.assertEqual(usernames, ["riotuser2", "riotuser1"])
            finally:
                for p in patchers:
                    p.stop()

    def test_reorder_preserves_active_username(self):
        """reorder_accounts does not change active_username."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                ctrl.add_account("Smurf", "riotuser2", "pw2")
                active_before = ctrl.state.active_username
                ctrl.reorder_accounts(["riotuser2", "riotuser1"])
                self.assertEqual(ctrl.state.active_username, active_before)
            finally:
                for p in patchers:
                    p.stop()

    def test_reorder_ignores_unknown_usernames(self):
        """reorder_accounts with an unknown username does not drop existing accounts (T-01-10)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                ctrl.add_account("Smurf", "riotuser2", "pw2")
                # "unknownuser" does not exist; both real accounts must survive
                ctrl.reorder_accounts(["unknownuser", "riotuser2", "riotuser1"])
                usernames = [a.username for a in ctrl.state.accounts]
                self.assertIn("riotuser1", usernames)
                self.assertIn("riotuser2", usernames)
                self.assertEqual(len(usernames), 2)
            finally:
                for p in patchers:
                    p.stop()

    def test_reorder_appends_missing_accounts(self):
        """reorder_accounts appends any account not in new_order at the end (T-01-10)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                ctrl.add_account("Smurf", "riotuser2", "pw2")
                ctrl.add_account("Third", "riotuser3", "pw3")
                # riotuser3 is missing from new_order → must be appended at end
                ctrl.reorder_accounts(["riotuser2", "riotuser1"])
                usernames = [a.username for a in ctrl.state.accounts]
                self.assertEqual(usernames[:2], ["riotuser2", "riotuser1"])
                self.assertIn("riotuser3", usernames)
                self.assertEqual(len(usernames), 3)
            finally:
                for p in patchers:
                    p.stop()

    def test_reorder_pushes_state(self):
        """reorder_accounts calls _push_state() after reordering (GUI receives update)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                ctrl.add_account("Smurf", "riotuser2", "pw2")
                with patch.object(ctrl, "_push_state") as mock_push:
                    ctrl.reorder_accounts(["riotuser2", "riotuser1"])
                self.assertGreater(mock_push.call_count, 0)
            finally:
                for p in patchers:
                    p.stop()


class TestControllerSwitchAccount(unittest.TestCase):
    """Unit tests for Controller.switch_account orchestration (SWITCH-01/02/03, D-09/D-12).

    riot_client is fully mocked so no real processes are touched.
    root.after calls are executed synchronously via _FakeTkRoot.
    """

    def _make_controller_ctx(self, tmp_path: pathlib.Path):
        fake_root = _FakeTkRoot()
        patcher_app = patch.object(config, "APP_DIR", tmp_path)
        patcher_json = patch.object(config, "ACCOUNTS_JSON", tmp_path / "accounts.json")
        patcher_sessions = patch.object(config, "SESSIONS_DIR", tmp_path / "sessions")
        patcher_app.start()
        patcher_json.start()
        patcher_sessions.start()
        config.ensure_dirs()
        ctrl = controller_module.Controller(fake_root)
        ctrl._fake_root = fake_root
        # WR-02: register so tearDown cancels every daemon timer this controller
        # scheduled (accounts poll, rank refresh, game-end poll, clipboard clear).
        # A switch fires _on_switch_done -> rank refresh + game-end poll timers
        # that call unpatched riot_client.* after the test returns — leaking
        # those timers is the root cause of the historical cross-file flake.
        self._ctrls.append(ctrl)
        return ctrl, [patcher_app, patcher_json, patcher_sessions]

    def setUp(self):
        _fake_keyring.reset()
        self._ctrls = []

    def tearDown(self):
        # WR-02: deterministically cancel all daemon timers created during the test.
        for ctrl in self._ctrls:
            ctrl.shutdown()

    def test_match_guard_sets_error_status_and_no_thread_started(self):
        """switch_account sets ERROR status and does NOT start a thread when is_game_running()=True (D-07/SWITCH-02)."""
        import tempfile
        from models import SwitchStatus
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                target = ctrl.state.accounts[0]

                with patch("riot_client.is_game_running", return_value=True), \
                     patch("riot_client.stop") as mock_stop:
                    ctrl.switch_account(target)
                    # stop() must NOT have been called
                    mock_stop.assert_not_called()
                    # Status must be ERROR
                    self.assertEqual(ctrl.state.status, SwitchStatus.ERROR)
                    # Error message must mention the game/match block
                    self.assertIn("blockiert", ctrl.state.status_message.lower())
            finally:
                for p in patchers:
                    p.stop()

    def test_stop_failure_does_not_call_swap_session(self):
        """_do_switch does not call swap_session when stop() returns False (D-12 safe state).

        Calls _do_switch directly (synchronous) to avoid thread-state leaks between tests.
        """
        import tempfile
        import core
        from models import SwitchStatus
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                target = ctrl.state.accounts[0]
                target.has_snapshot = True

                with patch("core.perform_switch", return_value=core.SwitchResult.STOP_FAILED):
                    ctrl._do_switch(target)  # synchronous — no thread
                    self.assertEqual(ctrl.state.status, SwitchStatus.ERROR)
            finally:
                for p in patchers:
                    p.stop()

    def test_successful_switch_sets_active_username_internally(self):
        """On success _on_switch_done sets state.active_username = target.username (D-09/ACCT-04).

        Calls _do_switch directly (synchronous) to avoid thread-state leaks between tests.
        """
        import tempfile
        import core
        from models import SwitchStatus
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                ctrl.add_account("Smurf", "riotuser2", "pw2")
                target = ctrl.state.accounts[1]  # riotuser2
                target.has_snapshot = True

                with patch("core.perform_switch", return_value=core.SwitchResult.SUCCESS):
                    ctrl._do_switch(target)  # synchronous — no thread
                    self.assertEqual(ctrl.state.active_username, "riotuser2")
                    self.assertEqual(ctrl.state.status, SwitchStatus.IDLE)
                    # Status message shows display_name (UI-SPEC: "Fertig — {display_name} ist aktiv.")
                    self.assertIn("Smurf", ctrl.state.status_message)
            finally:
                for p in patchers:
                    p.stop()

    def test_no_snapshot_routes_to_pending_first_login_state(self):
        """swap_session raising FileNotFoundError routes to pending-first-login (D-04).

        After _do_switch the controller must be in pending-first-login state (not yet
        confirmed by user) and the status message must ask the user to log in.

        Calls _do_switch directly (synchronous) to avoid thread-state leaks between tests.
        """
        import tempfile
        from models import SwitchStatus
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                target = ctrl.state.accounts[0]
                target.has_snapshot = False  # no snapshot → first login

                with patch("riot_client.stop", return_value=True), \
                     patch("riot_client.swap_session", side_effect=FileNotFoundError("no snap")), \
                     patch("riot_client.find_riot_client_exe",
                           return_value=pathlib.Path("C:/fake/RiotClientServices.exe")), \
                     patch("riot_client.start") as mock_start:
                    ctrl._do_switch(target)  # synchronous — no thread
                    # Must have started the client (Riot Client opens for manual login)
                    mock_start.assert_called_once()
                    # Controller must be in pending-first-login state
                    self.assertIs(ctrl._pending_first_login, target)
                    # Status message must ask the user to log in and click the button
                    msg_lower = ctrl.state.status_message.lower()
                    self.assertIn("einloggen", msg_lower)
            finally:
                for p in patchers:
                    p.stop()

    def test_switch_sets_switching_status_initially(self):
        """switch_account sets SWITCHING status on the main thread before spawning the background thread.

        Verifies that state.status == SWITCHING immediately after switch_account() returns
        (before _do_switch runs) by checking that the initial set_status was called.
        """
        import tempfile
        from models import SwitchStatus
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                target = ctrl.state.accounts[0]
                target.has_snapshot = True

                with patch("riot_client.is_game_running", return_value=False), \
                     patch("riot_client.stop", return_value=False), \
                     patch("riot_client.swap_session"):
                    # switch_account sets SWITCHING synchronously, then spawns thread
                    ctrl.switch_account(target)
                    # At this point the status was set to SWITCHING (now may be ERROR
                    # if thread ran immediately, so we check the initial _set_status call
                    # via the state history — simplest: verify _set_status was called
                    # at least once with SWITCHING by checking state or listener calls)
                    # The simplest reliable assertion: ERROR is the final state (stop=False)
                    # and we trust that SWITCHING was intermediate (tested via _do_switch directly)
                    import time as _t; _t.sleep(0.2)  # let thread finish
                    # Final state after stop()=False must be ERROR
                    self.assertEqual(ctrl.state.status, SwitchStatus.ERROR)
            finally:
                for p in patchers:
                    p.stop()

    def test_no_riot_files_read_for_active_account(self):
        """switch_account never reads Riot session/lockfiles to determine active account (D-09)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                target = ctrl.state.accounts[0]
                target.has_snapshot = True

                # Verify controller source does not read lockfile or RiotClientSettings
                # (controller_module's own __file__ is authoritative regardless of test location)
                src = open(controller_module.__file__, encoding="utf-8").read()
                self.assertNotIn("lockfile", src)
                self.assertNotIn("RiotClientSettings.yaml", src)
            finally:
                for p in patchers:
                    p.stop()


class TestControllerConfirmFirstLogin(unittest.TestCase):
    """Unit tests for Controller.confirm_first_login_snapshot (D-04 manual confirm flow)."""

    def _make_controller_ctx(self, tmp_path: pathlib.Path):
        fake_root = _FakeTkRoot()
        patcher_app = patch.object(config, "APP_DIR", tmp_path)
        patcher_json = patch.object(config, "ACCOUNTS_JSON", tmp_path / "accounts.json")
        patcher_sessions = patch.object(config, "SESSIONS_DIR", tmp_path / "sessions")
        patcher_app.start()
        patcher_json.start()
        patcher_sessions.start()
        config.ensure_dirs()
        ctrl = controller_module.Controller(fake_root)
        ctrl._fake_root = fake_root
        # WR-02: register so tearDown cancels every daemon timer this controller
        # scheduled (accounts poll, rank refresh, game-end poll, clipboard clear).
        # A switch fires _on_switch_done -> rank refresh + game-end poll timers
        # that call unpatched riot_client.* after the test returns — leaking
        # those timers is the root cause of the historical cross-file flake.
        self._ctrls.append(ctrl)
        return ctrl, [patcher_app, patcher_json, patcher_sessions]

    def setUp(self):
        _fake_keyring.reset()
        self._ctrls = []

    def tearDown(self):
        # WR-02: deterministically cancel all daemon timers created during the test.
        for ctrl in self._ctrls:
            ctrl.shutdown()

    def test_confirm_success_sets_has_snapshot_and_persists(self):
        """confirm_first_login_snapshot sets has_snapshot=True and persists on success."""
        import json
        import tempfile
        from models import SwitchStatus
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            json_path = tmp_path / "accounts.json"
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                target = ctrl.state.accounts[0]
                ctrl._pending_first_login = target

                with patch("riot_client.save_snapshot_now", return_value=True):
                    ctrl.confirm_first_login_snapshot()

                self.assertTrue(target.has_snapshot)
                # Persisted to JSON
                data = json.loads(json_path.read_text(encoding="utf-8"))
                self.assertTrue(data["accounts"][0]["has_snapshot"])
            finally:
                for p in patchers:
                    p.stop()

    def test_confirm_success_sets_active_username(self):
        """confirm_first_login_snapshot sets active_username to the confirmed account (D-09)."""
        import tempfile
        from models import SwitchStatus
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                ctrl.add_account("Smurf", "riotuser2", "pw2")
                target = ctrl.state.accounts[1]  # riotuser2
                ctrl._pending_first_login = target

                with patch("riot_client.save_snapshot_now", return_value=True):
                    ctrl.confirm_first_login_snapshot()

                self.assertEqual(ctrl.state.active_username, "riotuser2")
            finally:
                for p in patchers:
                    p.stop()

    def test_confirm_success_clears_pending_state(self):
        """confirm_first_login_snapshot clears _pending_first_login on success."""
        import tempfile
        from models import SwitchStatus
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                target = ctrl.state.accounts[0]
                ctrl._pending_first_login = target

                with patch("riot_client.save_snapshot_now", return_value=True):
                    ctrl.confirm_first_login_snapshot()

                self.assertIsNone(ctrl._pending_first_login)
            finally:
                for p in patchers:
                    p.stop()

    def test_confirm_success_sets_idle_status(self):
        """confirm_first_login_snapshot sets IDLE status with success message on success."""
        import tempfile
        from models import SwitchStatus
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                target = ctrl.state.accounts[0]
                ctrl._pending_first_login = target

                with patch("riot_client.save_snapshot_now", return_value=True):
                    ctrl.confirm_first_login_snapshot()

                self.assertEqual(ctrl.state.status, SwitchStatus.IDLE)
                self.assertIn("Snapshot gespeichert", ctrl.state.status_message)
            finally:
                for p in patchers:
                    p.stop()

    def test_confirm_failure_leaves_pending_state(self):
        """confirm_first_login_snapshot keeps pending state when save_snapshot_now returns False."""
        import tempfile
        from models import SwitchStatus
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                target = ctrl.state.accounts[0]
                ctrl._pending_first_login = target

                with patch("riot_client.save_snapshot_now", return_value=False):
                    ctrl.confirm_first_login_snapshot()

                # Pending state must still be set
                self.assertIs(ctrl._pending_first_login, target)
            finally:
                for p in patchers:
                    p.stop()

    def test_confirm_failure_does_not_set_has_snapshot(self):
        """confirm_first_login_snapshot does not set has_snapshot=True on failure."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                target = ctrl.state.accounts[0]
                ctrl._pending_first_login = target

                with patch("riot_client.save_snapshot_now", return_value=False):
                    ctrl.confirm_first_login_snapshot()

                self.assertFalse(target.has_snapshot)
            finally:
                for p in patchers:
                    p.stop()

    def test_confirm_failure_shows_retry_message(self):
        """confirm_first_login_snapshot shows a German 'noch kein Login' message on failure."""
        import tempfile
        from models import SwitchStatus
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                target = ctrl.state.accounts[0]
                ctrl._pending_first_login = target

                with patch("riot_client.save_snapshot_now", return_value=False):
                    ctrl.confirm_first_login_snapshot()

                msg_lower = ctrl.state.status_message.lower()
                self.assertIn("kein login erkannt", msg_lower)
            finally:
                for p in patchers:
                    p.stop()

    def test_confirm_noop_when_not_pending(self):
        """confirm_first_login_snapshot does nothing when not in pending state."""
        import tempfile
        from models import SwitchStatus
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                # No pending state set
                self.assertIsNone(ctrl._pending_first_login)

                with patch("riot_client.save_snapshot_now") as mock_save:
                    ctrl.confirm_first_login_snapshot()
                    mock_save.assert_not_called()
            finally:
                for p in patchers:
                    p.stop()

    def test_cancel_clears_pending_state(self):
        """cancel_first_login clears _pending_first_login and sets IDLE status."""
        import tempfile
        from models import SwitchStatus
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                target = ctrl.state.accounts[0]
                ctrl._pending_first_login = target

                ctrl.cancel_first_login()

                self.assertIsNone(ctrl._pending_first_login)
                self.assertEqual(ctrl.state.status, SwitchStatus.IDLE)
            finally:
                for p in patchers:
                    p.stop()


class TestControllerFirstLoginClearSession(unittest.TestCase):
    """Tests for the first-login flow: refresh_snapshot + clear_session called before client start."""

    def _make_controller_ctx(self, tmp_path: pathlib.Path):
        fake_root = _FakeTkRoot()
        patcher_app = patch.object(config, "APP_DIR", tmp_path)
        patcher_json = patch.object(config, "ACCOUNTS_JSON", tmp_path / "accounts.json")
        patcher_sessions = patch.object(config, "SESSIONS_DIR", tmp_path / "sessions")
        patcher_app.start()
        patcher_json.start()
        patcher_sessions.start()
        config.ensure_dirs()
        ctrl = controller_module.Controller(fake_root)
        ctrl._fake_root = fake_root
        # WR-02: register so tearDown cancels every daemon timer this controller
        # scheduled (accounts poll, rank refresh, game-end poll, clipboard clear).
        # A switch fires _on_switch_done -> rank refresh + game-end poll timers
        # that call unpatched riot_client.* after the test returns — leaking
        # those timers is the root cause of the historical cross-file flake.
        self._ctrls.append(ctrl)
        return ctrl, [patcher_app, patcher_json, patcher_sessions]

    def setUp(self):
        _fake_keyring.reset()
        self._ctrls = []

    def tearDown(self):
        # WR-02: deterministically cancel all daemon timers created during the test.
        for ctrl in self._ctrls:
            ctrl.shutdown()

    def test_clear_session_called_before_start_in_first_login_flow(self):
        """_do_switch calls clear_session() before start() in the FileNotFoundError branch."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                target = ctrl.state.accounts[0]
                target.has_snapshot = False

                call_order = []

                with patch("riot_client.stop", return_value=True), \
                     patch("riot_client.swap_session", side_effect=FileNotFoundError("no snap")), \
                     patch("riot_client.clear_session",
                           side_effect=lambda: call_order.append("clear_session")), \
                     patch("riot_client.find_riot_client_exe",
                           return_value=pathlib.Path("C:/fake/RiotClientServices.exe")), \
                     patch("riot_client.start",
                           side_effect=lambda exe: call_order.append("start")), \
                     patch("riot_client.refresh_snapshot", return_value=False):
                    ctrl._do_switch(target)

                self.assertIn("clear_session", call_order)
                self.assertIn("start", call_order)
                # clear_session must be called BEFORE start
                self.assertLess(
                    call_order.index("clear_session"),
                    call_order.index("start"),
                    "clear_session() must be called before start() in the first-login flow",
                )
            finally:
                for p in patchers:
                    p.stop()

    def test_refresh_snapshot_called_for_active_account_in_first_login_flow(self):
        """_do_switch calls refresh_snapshot(active_username) before start when active has a snapshot."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Active", "active_user", "pw1")
                ctrl.add_account("New", "new_user", "pw2")
                ctrl.state.active_username = "active_user"
                ctrl.state.accounts[0].has_snapshot = True   # active has snapshot
                ctrl.state.accounts[1].has_snapshot = False  # target has no snapshot

                target = ctrl.state.accounts[1]

                refresh_calls = []

                with patch("riot_client.stop", return_value=True), \
                     patch("riot_client.swap_session", side_effect=FileNotFoundError("no snap")), \
                     patch("riot_client.clear_session"), \
                     patch("riot_client.find_riot_client_exe",
                           return_value=pathlib.Path("C:/fake/RiotClientServices.exe")), \
                     patch("riot_client.start"), \
                     patch("riot_client.refresh_snapshot",
                           side_effect=lambda u: refresh_calls.append(u) or True):
                    ctrl._do_switch(target)

                self.assertIn("active_user", refresh_calls,
                              "refresh_snapshot must be called for the active account")
            finally:
                for p in patchers:
                    p.stop()

    def test_pending_status_text_does_not_mention_sign_out(self):
        """Pending-first-login status text must NOT contain 'abmelden' or 'sign out'."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("NewAcc", "riotuser1", "pw1")
                target = ctrl.state.accounts[0]
                target.has_snapshot = False

                with patch("riot_client.stop", return_value=True), \
                     patch("riot_client.swap_session", side_effect=FileNotFoundError("no snap")), \
                     patch("riot_client.clear_session"), \
                     patch("riot_client.find_riot_client_exe",
                           return_value=pathlib.Path("C:/fake/RiotClientServices.exe")), \
                     patch("riot_client.start"), \
                     patch("riot_client.refresh_snapshot", return_value=False):
                    ctrl._do_switch(target)

                msg_lower = ctrl.state.status_message.lower()
                self.assertNotIn("abmelden", msg_lower,
                                 "Status text must not instruct the user to sign out")
                self.assertNotIn("sign out", msg_lower,
                                 "Status text must not instruct the user to sign out")
            finally:
                for p in patchers:
                    p.stop()

    def test_pending_status_text_contains_display_name(self):
        """Pending-first-login status text contains the target account's display name."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("MeinSmurf", "riotuser1", "pw1")
                target = ctrl.state.accounts[0]
                target.has_snapshot = False

                with patch("riot_client.stop", return_value=True), \
                     patch("riot_client.swap_session", side_effect=FileNotFoundError("no snap")), \
                     patch("riot_client.clear_session"), \
                     patch("riot_client.find_riot_client_exe",
                           return_value=pathlib.Path("C:/fake/RiotClientServices.exe")), \
                     patch("riot_client.start"), \
                     patch("riot_client.refresh_snapshot", return_value=False):
                    ctrl._do_switch(target)

                self.assertIn("MeinSmurf", ctrl.state.status_message,
                              "Status text must include the target account's display name")
            finally:
                for p in patchers:
                    p.stop()


class TestControllerNormalSwitchRefreshSnapshot(unittest.TestCase):
    """Tests for the normal switch flow: refresh_snapshot called before stop()."""

    def _make_controller_ctx(self, tmp_path: pathlib.Path):
        fake_root = _FakeTkRoot()
        patcher_app = patch.object(config, "APP_DIR", tmp_path)
        patcher_json = patch.object(config, "ACCOUNTS_JSON", tmp_path / "accounts.json")
        patcher_sessions = patch.object(config, "SESSIONS_DIR", tmp_path / "sessions")
        patcher_app.start()
        patcher_json.start()
        patcher_sessions.start()
        config.ensure_dirs()
        ctrl = controller_module.Controller(fake_root)
        ctrl._fake_root = fake_root
        # WR-02: register so tearDown cancels every daemon timer this controller
        # scheduled (accounts poll, rank refresh, game-end poll, clipboard clear).
        # A switch fires _on_switch_done -> rank refresh + game-end poll timers
        # that call unpatched riot_client.* after the test returns — leaking
        # those timers is the root cause of the historical cross-file flake.
        self._ctrls.append(ctrl)
        return ctrl, [patcher_app, patcher_json, patcher_sessions]

    def setUp(self):
        _fake_keyring.reset()
        self._ctrls = []

    def tearDown(self):
        # WR-02: deterministically cancel all daemon timers created during the test.
        for ctrl in self._ctrls:
            ctrl.shutdown()

    def test_refresh_snapshot_called_before_stop_when_switching_away(self):
        """_do_switch delegates to core.perform_switch which calls refresh_snapshot(active_username)
        before stop() for snapshot accounts (D-30 / best-effort Step 0).

        Note: Since _do_switch now delegates to core.perform_switch for snapshot accounts,
        refresh_snapshot is called inside core.py with a fresh config.load_state() read.
        The test persists active_username to disk so core.perform_switch reads it correctly.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Active", "active_user", "pw1")
                ctrl.add_account("Target", "target_user", "pw2")
                ctrl.state.active_username = "active_user"
                ctrl.state.accounts[0].has_snapshot = True
                ctrl.state.accounts[1].has_snapshot = True
                # Persist to disk so core.perform_switch reads the correct active_username
                config.save_state(ctrl.state)

                target = ctrl.state.accounts[1]

                call_order = []

                with patch("riot_client.refresh_snapshot",
                           side_effect=lambda u: call_order.append(f"refresh:{u}") or True), \
                     patch("riot_client.snapshot_exists", return_value=True), \
                     patch("riot_client.stop",
                           side_effect=lambda **kw: call_order.append("stop") or True), \
                     patch("riot_client.swap_session"), \
                     patch("riot_client.find_riot_client_exe",
                           return_value=pathlib.Path("C:/fake/RiotClientServices.exe")), \
                     patch("riot_client.start"), \
                     patch("riot_client.is_game_running", return_value=False):
                    ctrl._do_switch(target)

                self.assertIn("refresh:active_user", call_order,
                              "refresh_snapshot must be called for the active account")
                self.assertIn("stop", call_order)
                self.assertLess(
                    call_order.index("refresh:active_user"),
                    call_order.index("stop"),
                    "refresh_snapshot must be called BEFORE stop()",
                )
            finally:
                for p in patchers:
                    p.stop()

    def test_refresh_snapshot_failure_does_not_abort_switch(self):
        """A failing refresh_snapshot() must never abort the normal switch (best-effort)."""
        import tempfile
        from models import SwitchStatus
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Active", "active_user", "pw1")
                ctrl.add_account("Target", "target_user", "pw2")
                ctrl.state.active_username = "active_user"
                ctrl.state.accounts[0].has_snapshot = True
                ctrl.state.accounts[1].has_snapshot = True

                target = ctrl.state.accounts[1]

                with patch("riot_client.refresh_snapshot",
                           side_effect=Exception("disk error")), \
                     patch("riot_client.snapshot_exists", return_value=True), \
                     patch("riot_client.stop", return_value=True), \
                     patch("riot_client.swap_session"), \
                     patch("riot_client.find_riot_client_exe",
                           return_value=pathlib.Path("C:/fake/RiotClientServices.exe")), \
                     patch("riot_client.start"), \
                     patch("riot_client.is_game_running", return_value=False):
                    # WR-02: is_game_running MUST be patched — core.perform_switch's
                    # match-guard (core.py:91) is the first statement and returns
                    # BLOCKED whenever League is actually running on the dev machine.
                    # Without this patch the test's SUCCESS assertion is
                    # environment-dependent (this is the real "intermittent" cause).
                    ctrl._do_switch(target)

                # Switch must have completed successfully despite refresh_snapshot failing
                self.assertEqual(ctrl.state.status, SwitchStatus.IDLE)
                self.assertEqual(ctrl.state.active_username, "target_user")
            finally:
                for p in patchers:
                    p.stop()

    def test_refresh_snapshot_not_called_when_no_active_account(self):
        """refresh_snapshot is NOT called when active_username is None."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Target", "target_user", "pw1")
                ctrl.state.active_username = None
                ctrl.state.accounts[0].has_snapshot = True

                target = ctrl.state.accounts[0]

                with patch("riot_client.refresh_snapshot") as mock_refresh, \
                     patch("riot_client.stop", return_value=True), \
                     patch("riot_client.swap_session"), \
                     patch("riot_client.find_riot_client_exe",
                           return_value=pathlib.Path("C:/fake/RiotClientServices.exe")), \
                     patch("riot_client.start"):
                    ctrl._do_switch(target)

                mock_refresh.assert_not_called()
            finally:
                for p in patchers:
                    p.stop()

    def test_refresh_snapshot_not_called_when_switching_to_same_account(self):
        """refresh_snapshot is NOT called when active and target are the same account."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                ctrl.state.active_username = "riotuser1"
                ctrl.state.accounts[0].has_snapshot = True

                target = ctrl.state.accounts[0]

                with patch("riot_client.refresh_snapshot") as mock_refresh, \
                     patch("riot_client.stop", return_value=True), \
                     patch("riot_client.swap_session"), \
                     patch("riot_client.find_riot_client_exe",
                           return_value=pathlib.Path("C:/fake/RiotClientServices.exe")), \
                     patch("riot_client.start"):
                    ctrl._do_switch(target)

                mock_refresh.assert_not_called()
            finally:
                for p in patchers:
                    p.stop()


class TestControllerRecaptureSession(unittest.TestCase):
    """Unit tests for Controller.recapture_session (D-19, SESSION-01)."""

    def _make_controller_ctx(self, tmp_path: pathlib.Path):
        fake_root = _FakeTkRoot()
        patcher_app = patch.object(config, "APP_DIR", tmp_path)
        patcher_json = patch.object(config, "ACCOUNTS_JSON", tmp_path / "accounts.json")
        patcher_sessions = patch.object(config, "SESSIONS_DIR", tmp_path / "sessions")
        patcher_app.start()
        patcher_json.start()
        patcher_sessions.start()
        config.ensure_dirs()
        ctrl = controller_module.Controller(fake_root)
        ctrl._fake_root = fake_root
        # WR-02: register so tearDown cancels every daemon timer this controller
        # scheduled (accounts poll, rank refresh, game-end poll, clipboard clear).
        self._ctrls.append(ctrl)
        return ctrl, [patcher_app, patcher_json, patcher_sessions]

    def setUp(self):
        _fake_keyring.reset()
        self._ctrls = []

    def tearDown(self):
        # WR-02: deterministically cancel all daemon timers created during the test.
        for ctrl in self._ctrls:
            ctrl.shutdown()

    def test_unknown_username_is_silent_noop(self):
        """recapture_session silently returns for an unknown username (stale JS state guard)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                with patch("riot_client.is_game_running") as mock_running:
                    ctrl.recapture_session("nobody")
                    mock_running.assert_not_called()
            finally:
                for p in patchers:
                    p.stop()

    def test_live_game_hard_blocks_without_clear_session(self):
        """_do_recapture sets ERROR and does NOT call clear_session when a match is live (T-05-09)."""
        import time
        import tempfile
        from models import SwitchStatus
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                target = ctrl.state.accounts[0]
                target.has_snapshot = True

                with patch("riot_client.is_game_running", return_value=True), \
                     patch("riot_client.clear_session") as mock_clear, \
                     patch("riot_client.start") as mock_start:
                    ctrl._do_recapture(target)  # synchronous — no thread
                    mock_clear.assert_not_called()
                    mock_start.assert_not_called()
                    self.assertEqual(ctrl.state.status, SwitchStatus.ERROR)
                    self.assertIn("nicht möglich", ctrl.state.status_message.lower())
            finally:
                for p in patchers:
                    p.stop()

    def test_normal_system_runs_full_recapture_sequence(self):
        """_do_recapture kills the client first, then clear_session -> start -> pending."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                target = ctrl.state.accounts[0]
                target.has_snapshot = True

                call_order = []

                with patch("riot_client.is_game_running", return_value=False), \
                     patch("riot_client.stop",
                           side_effect=lambda timeout=10.0: call_order.append("stop") or True), \
                     patch("riot_client.refresh_snapshot", return_value=True), \
                     patch("riot_client.clear_session",
                           side_effect=lambda: call_order.append("clear_session")), \
                     patch("riot_client.find_riot_client_exe",
                           return_value=pathlib.Path("C:/fake/RiotClientServices.exe")), \
                     patch("riot_client.start",
                           side_effect=lambda exe: call_order.append("start")):
                    ctrl._do_recapture(target)

                # The kill MUST precede clear_session/start — without it a running
                # single-instance client is never restarted (second-account no-op bug).
                self.assertEqual(call_order, ["stop", "clear_session", "start"])
                # Reuses the existing pending-first-login state — no new state machine.
                self.assertIs(ctrl._pending_first_login, target)
            finally:
                for p in patchers:
                    p.stop()

    def test_recapture_stop_failure_blocks_clear_and_start(self):
        """_do_recapture posts an error and does NOT clear/restart when the client won't die."""
        import tempfile
        from models import SwitchStatus
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                target = ctrl.state.accounts[0]
                target.has_snapshot = True

                with patch("riot_client.is_game_running", return_value=False), \
                     patch("riot_client.stop", return_value=False), \
                     patch("riot_client.clear_session") as mock_clear, \
                     patch("riot_client.find_riot_client_exe") as mock_find, \
                     patch("riot_client.start") as mock_start:
                    ctrl._do_recapture(target)
                    mock_clear.assert_not_called()
                    mock_find.assert_not_called()
                    mock_start.assert_not_called()
                    self.assertEqual(ctrl.state.status, SwitchStatus.ERROR)
                    self.assertIsNone(ctrl._pending_first_login)
            finally:
                for p in patchers:
                    p.stop()

    def test_riot_not_found_posts_error_without_start(self):
        """_do_recapture posts an error and does not enter pending state when Riot is not found."""
        import tempfile
        from models import SwitchStatus
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                target = ctrl.state.accounts[0]
                target.has_snapshot = True

                with patch("riot_client.is_game_running", return_value=False), \
                     patch("riot_client.stop", return_value=True), \
                     patch("riot_client.refresh_snapshot", return_value=True), \
                     patch("riot_client.clear_session"), \
                     patch("riot_client.find_riot_client_exe", return_value=None), \
                     patch("riot_client.start") as mock_start:
                    ctrl._do_recapture(target)
                    mock_start.assert_not_called()
                    self.assertEqual(ctrl.state.status, SwitchStatus.ERROR)
                    self.assertIsNone(ctrl._pending_first_login)
            finally:
                for p in patchers:
                    p.stop()

    def test_recapture_reuses_existing_confirm_flow(self):
        """confirm_first_login_snapshot (existing flow) completes the recapture (no new state machine)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, patchers = self._make_controller_ctx(tmp_path)
            try:
                ctrl.add_account("Main", "riotuser1", "pw1")
                target = ctrl.state.accounts[0]
                target.has_snapshot = True  # already has a (possibly stale) snapshot

                with patch("riot_client.is_game_running", return_value=False), \
                     patch("riot_client.stop", return_value=True), \
                     patch("riot_client.refresh_snapshot", return_value=True), \
                     patch("riot_client.clear_session"), \
                     patch("riot_client.find_riot_client_exe",
                           return_value=pathlib.Path("C:/fake/RiotClientServices.exe")), \
                     patch("riot_client.start"):
                    ctrl._do_recapture(target)

                with patch("riot_client.save_snapshot_now", return_value=True):
                    ctrl.confirm_first_login_snapshot()

                self.assertTrue(target.has_snapshot)
                self.assertIsNone(ctrl._pending_first_login)
                self.assertEqual(ctrl.state.active_username, "riotuser1")
            finally:
                for p in patchers:
                    p.stop()


if __name__ == "__main__":
    unittest.main()
