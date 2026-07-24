"""Tests fuer Plan 03-02 Task 2: controller._do_switch nutzt core.perform_switch
und accounts.json mtime-Watcher.

Testet:
  - _do_switch leitet an core.perform_switch weiter (Snapshot-Accounts)
  - _do_switch erhaelt First-Login-Flow fuer Accounts ohne Snapshot
  - Externe accounts.json-Aenderung -> _poll_accounts_json laedt neu + notifiziert
  - Eigener save_state aktualisiert _accounts_json_mtime (kein Write-Loop)
  - shutting_down=True -> _poll_accounts_json macht nichts

Anti-Pattern-Guard: Dieser Testfile importiert KEIN customtkinter direkt.
"""
from __future__ import annotations

import importlib
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Fake keyring — verhindert echte WCM-Zugriffe
# ---------------------------------------------------------------------------

class _FakeKeyringErrors:
    class PasswordDeleteError(Exception):
        pass


class _FakeKeyring:
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
        del self._store[key]

    def reset(self) -> None:
        self._store.clear()


_fake_keyring = _FakeKeyring()

# Inject fake keyring BEVOR controller oder credential_store geladen werden
_keyring_mod = types.ModuleType("keyring")
_keyring_mod.set_password = _fake_keyring.set_password
_keyring_mod.get_password = _fake_keyring.get_password
_keyring_mod.delete_password = _fake_keyring.delete_password
_keyring_errors_mod = types.ModuleType("keyring.errors")
_keyring_errors_mod.PasswordDeleteError = _FakeKeyringErrors.PasswordDeleteError
_keyring_mod.errors = _keyring_errors_mod
sys.modules["keyring"] = _keyring_mod
sys.modules["keyring.errors"] = _keyring_errors_mod

# Force-reload credential_store
if "credential_store" in sys.modules:
    importlib.reload(sys.modules["credential_store"])

# ---------------------------------------------------------------------------
# Imports nach keyring-Stub
# ---------------------------------------------------------------------------

import config
import credential_store
from models import Account, AppState, SwitchStatus

# ---------------------------------------------------------------------------
# FakeRoot — after(delay, fn) fuer Tests
# ---------------------------------------------------------------------------

class _FakeWindowState:
    """Minimal pywebview window.state stub for tests."""
    accounts = None
    active_username = None
    status = None
    status_message = None
    pending_first_login = None


class _FakeRoot:
    """Fake pywebview Window fuer Tests.

    Has a .state attribute for Controller._push_state().
    Legacy .after() method kept for compatibility but is a no-op in v2.0
    (threading.Timer is now used instead of root.after).
    """

    def __init__(self, execute_nonzero_after: bool = False):
        self.state = _FakeWindowState()
        self._after_calls: list[tuple] = []
        self._execute_nonzero = execute_nonzero_after

    def after(self, delay, fn, *args):
        self._after_calls.append((delay, fn))
        if delay == 0 or self._execute_nonzero:
            fn(*args)
        return "after_id"

    def after_cancel(self, after_id):
        pass

    def clipboard_clear(self):
        pass

    def clipboard_append(self, value):
        pass

    def clipboard_get(self):
        return ""


# ---------------------------------------------------------------------------
# Helper: Controller mit tmp-config erzeugen
# ---------------------------------------------------------------------------

def _make_tmp_config(tmp_dir: pathlib.Path):
    """Redirect config-Pfade auf tmp-Verzeichnis."""
    config.APP_DIR = tmp_dir
    config.ACCOUNTS_JSON = tmp_dir / "accounts.json"
    config.SESSIONS_DIR = tmp_dir / "sessions"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    (tmp_dir / "sessions").mkdir(exist_ok=True)


def _make_controller(tmp: pathlib.Path, accounts: list[Account],
                     active_username: str | None = None,
                     fake_root_execute_nonzero: bool = False):
    """Erstelle einen Controller mit den gegebenen Accounts in tmp-Konfiguration."""
    _make_tmp_config(tmp)
    state = AppState(accounts=accounts, active_username=active_username)
    config.save_state(state)

    # Reload controller so it picks up the fresh config state
    import controller as ctrl_mod
    fake_root = _FakeRoot(execute_nonzero_after=fake_root_execute_nonzero)

    with patch("riot_client.is_game_running", return_value=False):
        ctrl = ctrl_mod.Controller(fake_root)
    return ctrl, fake_root


# ===========================================================================
# Task 2A Tests: _do_switch delegiert an core.perform_switch
# ===========================================================================

class TestDoSwitchDelegatesToCore(unittest.TestCase):
    """_do_switch delegiert Snapshot-Accounts an core.perform_switch."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        _fake_keyring.reset()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_do_switch_calls_perform_switch_for_snapshot_account(self):
        """_do_switch ruft core.perform_switch(target.username) bei Snapshot-Account auf."""
        import controller as ctrl_mod
        from core import SwitchResult

        accounts = [
            Account("alice", "Alice", has_snapshot=True),
            Account("bob", "Bob", has_snapshot=True),
        ]
        ctrl, fake_root = _make_controller(self.tmp, accounts, active_username="bob")

        target = accounts[0]  # alice — hat Snapshot
        with patch("core.perform_switch", return_value=SwitchResult.SUCCESS) as mock_ps, \
             patch("config.save_state"), \
             patch("riot_client.is_game_running", return_value=False):
            ctrl._do_switch(target)
        mock_ps.assert_called_once_with("alice")

    def test_do_switch_success_posts_on_switch_done(self):
        """SUCCESS -> _on_switch_done wird via root.after(0) gepostet."""
        import controller as ctrl_mod
        from core import SwitchResult

        accounts = [Account("alice", "Alice", has_snapshot=True)]
        ctrl, fake_root = _make_controller(self.tmp, accounts, active_username="alice")

        target = accounts[0]
        on_switch_done_called = []
        original = ctrl._on_switch_done

        def fake_on_switch_done(t):
            on_switch_done_called.append(t)

        ctrl._on_switch_done = fake_on_switch_done

        with patch("core.perform_switch", return_value=SwitchResult.SUCCESS), \
             patch("config.save_state"), \
             patch("riot_client.is_game_running", return_value=False):
            ctrl._do_switch(target)

        # _on_switch_done wird via root.after(0,...) gepostet
        # FakeRoot mit delay=0 fuehrt es sofort aus
        self.assertEqual(len(on_switch_done_called), 1)
        self.assertEqual(on_switch_done_called[0].username, "alice")

    def test_do_switch_blocked_posts_error_no_on_switch_done(self):
        """BLOCKED -> _post_error gesetzt, _on_switch_done NICHT aufgerufen."""
        import controller as ctrl_mod
        from core import SwitchResult

        accounts = [Account("alice", "Alice", has_snapshot=True)]
        ctrl, fake_root = _make_controller(self.tmp, accounts, active_username="alice")
        target = accounts[0]

        on_switch_done_called = []
        ctrl._on_switch_done = lambda t: on_switch_done_called.append(t)

        with patch("core.perform_switch", return_value=SwitchResult.BLOCKED), \
             patch("riot_client.is_game_running", return_value=False):
            ctrl._do_switch(target)

        self.assertEqual(on_switch_done_called, [])
        # Status soll ERROR sein
        self.assertEqual(ctrl.state.status, SwitchStatus.ERROR)

    def test_do_switch_no_snapshot_posts_error(self):
        """NO_SNAPSHOT (via core) -> Error gepostet."""
        import controller as ctrl_mod
        from core import SwitchResult

        # Account mit has_snapshot=True damit er durch core.perform_switch geht
        accounts = [Account("alice", "Alice", has_snapshot=True)]
        ctrl, fake_root = _make_controller(self.tmp, accounts, active_username="alice")
        target = accounts[0]

        with patch("core.perform_switch", return_value=SwitchResult.NO_SNAPSHOT), \
             patch("riot_client.is_game_running", return_value=False):
            ctrl._do_switch(target)

        self.assertEqual(ctrl.state.status, SwitchStatus.ERROR)

    def test_do_switch_preserves_first_login_flow_for_no_snapshot_account(self):
        """Account ohne Snapshot -> First-Login-Flow (clear_session + enter_pending), KEIN core.perform_switch."""
        import controller as ctrl_mod

        accounts = [Account("newbie", "Newbie", has_snapshot=False)]
        ctrl, fake_root = _make_controller(self.tmp, accounts, active_username=None)
        target = accounts[0]

        enter_pending_called = []
        ctrl._enter_pending_first_login = lambda t: enter_pending_called.append(t)

        with patch("core.perform_switch") as mock_ps, \
             patch("riot_client.stop", return_value=True), \
             patch("riot_client.clear_session"), \
             patch("riot_client.find_riot_client_exe", return_value=pathlib.Path("RiotClientServices.exe")), \
             patch("riot_client.start"), \
             patch("riot_client.refresh_snapshot"), \
             patch("riot_client.is_game_running", return_value=False):
            ctrl._do_switch(target)

        # Fuer Account OHNE Snapshot darf core.perform_switch NICHT aufgerufen werden
        mock_ps.assert_not_called()
        # First-Login-Flow wurde betreten
        self.assertEqual(len(enter_pending_called), 1)

    def test_switch_account_match_guard_hard_block(self):
        """switch_account: Match-Guard blockt harkt wenn League laeuft (D-07)."""
        import controller as ctrl_mod

        accounts = [Account("alice", "Alice", has_snapshot=True)]
        ctrl, fake_root = _make_controller(self.tmp, accounts, active_username="alice")
        target = accounts[0]

        with patch("riot_client.is_game_running", return_value=True):
            ctrl.switch_account(target)

        self.assertEqual(ctrl.state.status, SwitchStatus.ERROR)
        self.assertIn("blockiert", ctrl.state.status_message.lower())


# ===========================================================================
# Task 2B Tests: mtime-Watcher fuer accounts.json
# ===========================================================================

class TestAccountsJsonMtimeWatcher(unittest.TestCase):
    """Externe accounts.json-Aenderungen werden via mtime-Poll erkannt."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        _fake_keyring.reset()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_poll_detects_external_change_and_pushes_state(self):
        """Externe Aenderung (mtime aendert sich, active_username anders) -> reload + _push_state."""
        import controller as ctrl_mod

        accounts = [
            Account("alice", "Alice", has_snapshot=True),
            Account("bob", "Bob", has_snapshot=True),
        ]
        ctrl, fake_root = _make_controller(self.tmp, accounts, active_username="alice")

        # Externe Aenderung: schreibe accounts.json mit anderem active_username
        new_state = AppState(accounts=accounts, active_username="bob")
        config.save_state(new_state)

        # Stelle sicher dass mtime anders ist als _accounts_json_mtime im Controller
        ctrl._accounts_json_mtime = 0.0

        # Direkt poll aufrufen
        ctrl._poll_accounts_json()

        self.assertEqual(ctrl.state.active_username, "bob")
        # _push_state was called — window.state.active_username updated
        self.assertEqual(fake_root.state.active_username, "bob")

    def test_poll_own_write_does_not_trigger_reload(self):
        """Eigener save_state aktualisiert _accounts_json_mtime sofort (kein Write-Loop)."""
        import controller as ctrl_mod
        from unittest.mock import patch

        accounts = [Account("alice", "Alice", has_snapshot=True)]
        ctrl, fake_root = _make_controller(self.tmp, accounts, active_username="alice")

        # Simulate: controller schreibt selbst (wie _on_switch_done tut)
        ctrl.state.active_username = "alice"
        config.save_state(ctrl.state)
        # Nach eigenem Write muss _accounts_json_mtime aktualisiert sein
        ctrl._accounts_json_mtime = ctrl._get_accounts_mtime()

        # Poll: mtime stimmt jetzt ueberein -> kein Reload, kein _push_state
        with patch.object(ctrl, "_push_state") as mock_push:
            ctrl._poll_accounts_json()

        self.assertEqual(mock_push.call_count, 0,
                         "kein _push_state erwartet — eigener Write soll kein Reload ausloesen")

    def test_poll_shutting_down_does_nothing(self):
        """shutting_down=True -> _poll_accounts_json macht nichts und reschedult nicht."""
        import controller as ctrl_mod
        from unittest.mock import patch

        accounts = [Account("alice", "Alice", has_snapshot=True)]
        ctrl, fake_root = _make_controller(self.tmp, accounts, active_username="alice")
        ctrl._shutting_down = True

        # Externe Aenderung (wird ignoriert wenn shutting_down)
        ctrl._accounts_json_mtime = 0.0

        with patch.object(ctrl, "_push_state") as mock_push, \
             patch("threading.Timer") as mock_timer:
            ctrl._poll_accounts_json()

        self.assertEqual(mock_push.call_count, 0, "kein _push_state bei shutting_down")
        # Kein weiteres reschedule via threading.Timer
        mock_timer.assert_not_called()

    def test_poll_reschedules_itself(self):
        """_poll_accounts_json schedult sich nach Ausfuehrung erneut (Loop via threading.Timer)."""
        import controller as ctrl_mod
        from unittest.mock import patch

        accounts = [Account("alice", "Alice", has_snapshot=True)]
        ctrl, fake_root = _make_controller(self.tmp, accounts, active_username="alice")

        # Kein Unterschied in mtime -> kein _push_state, aber reschedule via threading.Timer
        with patch("threading.Timer") as mock_timer:
            mock_timer_instance = MagicMock()
            mock_timer.return_value = mock_timer_instance
            ctrl._poll_accounts_json()

        # threading.Timer wurde mit _schedule_accounts_poll-Aufruf erstellt
        mock_timer.assert_called()
        mock_timer_instance.start.assert_called()

    def test_get_accounts_mtime_returns_float(self):
        """_get_accounts_mtime() gibt float zurueck (0.0 wenn Datei nicht existiert)."""
        import controller as ctrl_mod

        accounts = [Account("alice", "Alice", has_snapshot=True)]
        ctrl, fake_root = _make_controller(self.tmp, accounts, active_username="alice")

        mtime = ctrl._get_accounts_mtime()
        self.assertIsInstance(mtime, float)
        self.assertGreater(mtime, 0.0)  # Datei wurde in setUp geschrieben

    def test_get_accounts_mtime_returns_zero_if_missing(self):
        """_get_accounts_mtime() gibt 0.0 zurueck wenn accounts.json fehlt."""
        import controller as ctrl_mod

        accounts = [Account("alice", "Alice", has_snapshot=True)]
        ctrl, fake_root = _make_controller(self.tmp, accounts, active_username="alice")

        # Loesche accounts.json
        config.ACCOUNTS_JSON.unlink(missing_ok=True)

        mtime = ctrl._get_accounts_mtime()
        self.assertEqual(mtime, 0.0)

    def test_controller_has_poll_interval_constant(self):
        """Controller._POLL_INTERVAL_MS ist gesetzt (2000ms)."""
        import controller as ctrl_mod
        self.assertEqual(ctrl_mod.Controller._POLL_INTERVAL_MS, 2000)

    def test_controller_has_accounts_json_mtime_attribute(self):
        """Controller hat _accounts_json_mtime als float-Attribut nach __init__."""
        import controller as ctrl_mod

        accounts = [Account("alice", "Alice", has_snapshot=True)]
        ctrl, _ = _make_controller(self.tmp, accounts, active_username="alice")

        self.assertTrue(hasattr(ctrl, "_accounts_json_mtime"),
                        "Controller muss _accounts_json_mtime haben")
        self.assertIsInstance(ctrl._accounts_json_mtime, float)


# ===========================================================================
# Task 2C Tests: Loop-Schutz — save_state aktualisiert _accounts_json_mtime
# ===========================================================================

class TestLoopProtection(unittest.TestCase):
    """Nach config.save_state im Controller wird _accounts_json_mtime sofort aktualisiert."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        _fake_keyring.reset()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_on_switch_done_updates_accounts_json_mtime(self):
        """_on_switch_done: nach config.save_state wird _accounts_json_mtime aktualisiert."""
        import controller as ctrl_mod

        accounts = [Account("alice", "Alice", has_snapshot=True)]
        ctrl, fake_root = _make_controller(self.tmp, accounts, active_username="alice")

        # Setze _accounts_json_mtime auf alten Wert
        old_mtime = 0.0
        ctrl._accounts_json_mtime = old_mtime

        target = accounts[0]
        # _on_switch_done schreibt state und soll _accounts_json_mtime aktualisieren
        with patch("rank_service.resolve_puuid", side_effect=RuntimeError("no api")):
            ctrl._on_switch_done(target)

        # Nach _on_switch_done muss _accounts_json_mtime != 0.0 sein
        self.assertNotEqual(ctrl._accounts_json_mtime, old_mtime,
                            "_accounts_json_mtime muss nach save_state in _on_switch_done aktualisiert sein")

    def test_confirm_first_login_snapshot_updates_accounts_json_mtime(self):
        """confirm_first_login_snapshot: nach config.save_state wird _accounts_json_mtime aktualisiert."""
        import controller as ctrl_mod

        accounts = [Account("newbie", "Newbie", has_snapshot=False)]
        ctrl, fake_root = _make_controller(self.tmp, accounts, active_username=None)
        ctrl._pending_first_login = accounts[0]

        old_mtime = 0.0
        ctrl._accounts_json_mtime = old_mtime

        with patch("riot_client.save_snapshot_now", return_value=True):
            ctrl.confirm_first_login_snapshot()

        self.assertNotEqual(ctrl._accounts_json_mtime, old_mtime,
                            "_accounts_json_mtime muss nach save_state in confirm_first_login_snapshot aktualisiert sein")


if __name__ == "__main__":
    unittest.main()
