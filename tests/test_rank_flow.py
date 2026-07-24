"""Tests for Phase 2 Plan 03: Account model fields, config migration, Riot-ID/region
capture & validation in the controller, and rank-fetch orchestration.

Test pattern:
- Fake keyring (never touches WCM)
- Tmp-dir config (redirected away from real %APPDATA%)
- Fake root whose after(delay, fn) executes fn immediately (for timer tests)
- rank_service mocked to avoid live API calls
- threading.Thread stubbed to run target synchronously in some tests
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import threading
import types
import unittest
from unittest.mock import MagicMock, call, patch


# ---------------------------------------------------------------------------
# Fake keyring — same pattern as test_account_mgmt.py
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
        del self._store[(key)]

    def reset(self) -> None:
        self._store.clear()


_fake_keyring = _FakeKeyring()

# Inject fake keyring into sys.modules BEFORE importing credential_store.
# Note: test_account_mgmt.py may already have installed a different fake keyring
# in sys.modules["keyring"]. We replace it with ours AND then reload
# credential_store so it picks up our fake keyring rather than the prior one.
_keyring_mod = types.ModuleType("keyring")
_keyring_mod.set_password = _fake_keyring.set_password
_keyring_mod.get_password = _fake_keyring.get_password
_keyring_mod.delete_password = _fake_keyring.delete_password
_keyring_errors_mod = types.ModuleType("keyring.errors")
_keyring_errors_mod.PasswordDeleteError = _FakeKeyringErrors.PasswordDeleteError
_keyring_mod.errors = _keyring_errors_mod
sys.modules["keyring"] = _keyring_mod
sys.modules["keyring.errors"] = _keyring_errors_mod

# Force-reload credential_store so it re-imports 'keyring' from sys.modules
# (avoids stale reference to a prior test file's fake keyring instance).
import importlib
if "credential_store" in sys.modules:
    importlib.reload(sys.modules["credential_store"])

# ---------------------------------------------------------------------------
# Fake root — after(delay, fn) executes fn immediately; clipboard no-ops
# ---------------------------------------------------------------------------

class _FakeWindowState:
    """Minimal pywebview window.state stub."""
    accounts = None
    active_username = None
    status = None
    status_message = None
    pending_first_login = None


class _FakeRoot:
    def __init__(self):
        self.state = _FakeWindowState()
        self._after_calls: list[tuple] = []

    def after(self, delay, fn, *args):
        # Legacy no-op; threading.Timer is used in v2.0
        self._after_calls.append((delay, fn))
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
# Now import the real modules (keyring already replaced)
# ---------------------------------------------------------------------------

import config
import credential_store
from models import Account, AppState, RankInfo, QueueRank


# ---------------------------------------------------------------------------
# Helper: redirect config paths to a tmp directory
# ---------------------------------------------------------------------------

def _make_tmp_config(tmp_dir: pathlib.Path):
    """Monkey-patch config.py to use tmp_dir."""
    config.APP_DIR = tmp_dir
    config.ACCOUNTS_JSON = tmp_dir / "accounts.json"
    config.SESSIONS_DIR = tmp_dir / "sessions"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    (tmp_dir / "sessions").mkdir(exist_ok=True)


# ===========================================================================
# Task 1 Tests: Account fields, config migration, Riot-ID capture & validation
# ===========================================================================

class TestMigration(unittest.TestCase):
    """config.load_state backward-compatible migration from Phase-1 accounts.json."""

    def setUp(self):
        import tempfile
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        _make_tmp_config(self.tmp)
        _fake_keyring.reset()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        # Restore config paths to something safe
        config.APP_DIR = self.tmp
        config.ACCOUNTS_JSON = self.tmp / "accounts.json"
        config.SESSIONS_DIR = self.tmp / "sessions"

    def test_migration_phase1_json_loads_with_defaults(self):
        """Phase-1 accounts.json (no riot_id/region/puuid/rank_cache) loads without error."""
        phase1_data = {
            "accounts": [
                {
                    "username": "player1",
                    "display_name": "Main",
                    "has_snapshot": False,
                }
            ],
            "active_username": "player1",
        }
        config.ACCOUNTS_JSON.write_text(
            json.dumps(phase1_data), encoding="utf-8"
        )
        state = config.load_state()
        self.assertEqual(len(state.accounts), 1)
        acc = state.accounts[0]
        self.assertIsNone(acc.riot_id)
        # Phase 8 (REGION-02/D-12): the missing-region default is now the
        # canonical platform id "EUW1", not the legacy bare "EUW".
        self.assertEqual(acc.region, "EUW1")
        self.assertIsNone(acc.puuid)
        self.assertIsNone(acc.rank_cache)
        self.assertIsNone(acc.rank_cache_ts)

    def test_migration_empty_string_riot_id_becomes_none(self):
        """Empty string riot_id in JSON becomes None (defensive .get() or None)."""
        data = {
            "accounts": [
                {
                    "username": "p",
                    "display_name": "P",
                    "has_snapshot": False,
                    "riot_id": "",
                }
            ],
            "active_username": None,
        }
        config.ACCOUNTS_JSON.write_text(json.dumps(data), encoding="utf-8")
        state = config.load_state()
        self.assertIsNone(state.accounts[0].riot_id)

    def test_roundtrip_all_five_new_fields(self):
        """save_state then load_state round-trips riot_id, region, puuid, rank_cache, rank_cache_ts."""
        state = AppState(
            accounts=[
                Account(
                    username="player1",
                    display_name="Main",
                    has_snapshot=True,
                    riot_id="Main#EUW",
                    region="EUW",
                    puuid="abc123puuid",
                    rank_cache={"solo": {"tier": "GOLD", "division": "II", "lp": 47, "wins": 10, "losses": 8}},
                    rank_cache_ts=1234567890.0,
                )
            ],
            active_username="player1",
        )
        config.save_state(state)
        loaded = config.load_state()
        acc = loaded.accounts[0]
        self.assertEqual(acc.riot_id, "Main#EUW")
        # Phase 8 (REGION-02/D-12): legacy "EUW" is silently migrated to "EUW1"
        # on load — this round-trip proves the migration is transparent even
        # when the in-memory Account was constructed with the legacy value.
        self.assertEqual(acc.region, "EUW1")
        self.assertEqual(acc.puuid, "abc123puuid")
        self.assertIsNotNone(acc.rank_cache)
        self.assertEqual(acc.rank_cache_ts, 1234567890.0)

    def test_accounts_json_never_contains_password(self):
        """accounts.json must never contain a 'password' key (security invariant)."""
        state = AppState(
            accounts=[
                Account(username="u", display_name="D", has_snapshot=False)
            ]
        )
        config.save_state(state)
        raw = config.ACCOUNTS_JSON.read_text(encoding="utf-8")
        self.assertNotIn('"password"', raw)

    def test_accounts_json_rank_cache_ts_is_none_when_not_set(self):
        """rank_cache_ts writes as null when not set (not an error)."""
        state = AppState(
            accounts=[
                Account(username="u", display_name="D", has_snapshot=False,
                        rank_cache=None, rank_cache_ts=None)
            ]
        )
        config.save_state(state)
        raw = config.ACCOUNTS_JSON.read_text(encoding="utf-8")
        data = json.loads(raw)
        acc_data = data["accounts"][0]
        self.assertIn("rank_cache", acc_data)
        self.assertIn("rank_cache_ts", acc_data)
        self.assertIsNone(acc_data["rank_cache"])
        self.assertIsNone(acc_data["rank_cache_ts"])


class TestRiotIdValidation(unittest.TestCase):
    """controller.add_account with riot_id: validation paths."""

    def _make_controller(self, tmp: pathlib.Path, fake_root=None):
        """Create a controller with a fresh fake root and tmp config."""
        _make_tmp_config(tmp)
        if fake_root is None:
            fake_root = _FakeRoot()
        # Import fresh (or reload to pick up tmp config)
        import importlib
        import controller as ctrl_mod
        importlib.reload(ctrl_mod)
        from controller import Controller
        return Controller(fake_root), fake_root, ctrl_mod

    def setUp(self):
        import tempfile
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        _fake_keyring.reset()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_account_signature_accepts_riot_id_and_region(self):
        """Controller.add_account accepts riot_id and region kwargs without error."""
        _make_tmp_config(self.tmp)
        import importlib
        import controller as ctrl_mod
        importlib.reload(ctrl_mod)
        from controller import Controller
        ctrl = Controller(_FakeRoot())
        # Should not raise; no API key stored so deferred validation
        ctrl.add_account("Main", "player1", "password123", riot_id=None, region="EUW")
        self.assertEqual(len(ctrl.state.accounts), 1)

    def test_add_account_no_riot_id_works_as_before(self):
        """add_account without riot_id still works (backward compat)."""
        _make_tmp_config(self.tmp)
        import importlib
        import controller as ctrl_mod
        importlib.reload(ctrl_mod)
        from controller import Controller
        ctrl = Controller(_FakeRoot())
        ctrl.add_account("Main", "player1", "secret")
        acc = ctrl.state.accounts[0]
        self.assertIsNone(acc.riot_id)
        # Phase 8 (T-08-08): the "EUW" default is normalized to canonical "EUW1"
        # by the region-whitelist check in add_account.
        self.assertEqual(acc.region, "EUW1")

    def test_add_account_riot_id_with_api_key_resolves_puuid(self):
        """With a present API key, add_account resolves puuid via rank_service."""
        _make_tmp_config(self.tmp)
        # Store an API key
        credential_store.save_api_key("test-api-key-123")
        import importlib
        import controller as ctrl_mod
        importlib.reload(ctrl_mod)
        from controller import Controller

        ctrl = Controller(_FakeRoot())

        with patch("rank_service.resolve_puuid", return_value="fake-puuid-xyz") as mock_resolve:
            ctrl.add_account("Main", "player1", "password", riot_id="Main#EUW", region="EUW")
            # Phase 8 (T-08-08): region is normalized ("EUW"->"EUW1") and threaded
            # into resolve_puuid as platform_id (account-v1 routing).
            mock_resolve.assert_called_once_with(
                "Main", "EUW", "test-api-key-123", platform_id="EUW1"
            )

        acc = ctrl.state.accounts[0]
        self.assertEqual(acc.puuid, "fake-puuid-xyz")
        self.assertEqual(acc.riot_id, "Main#EUW")

    def test_add_account_riot_id_404_raises_and_does_not_append(self):
        """A 404 from resolve_puuid raises ValueError and does NOT add the account."""
        _make_tmp_config(self.tmp)
        credential_store.save_api_key("test-api-key")
        import importlib
        import controller as ctrl_mod
        importlib.reload(ctrl_mod)
        from controller import Controller
        from rank_service import RiotAPIError

        ctrl = Controller(_FakeRoot())

        with patch("rank_service.resolve_puuid", side_effect=RiotAPIError(404, "not found")):
            with self.assertRaises(ValueError) as ctx:
                ctrl.add_account("Main", "player1", "password",
                                 riot_id="NotExist#EUW", region="EUW")
            self.assertIn("nicht gefunden", str(ctx.exception))

        # Account must NOT be in the list
        self.assertEqual(len(ctrl.state.accounts), 0)
        # Credential must be cleaned up (no orphaned secret)
        self.assertFalse(bool(credential_store.get("player1")))

    def test_add_account_network_error_raises_valueerror_and_rolls_back(self):
        """CR-02: a requests network error during resolve raises ValueError and rolls back the credential."""
        _make_tmp_config(self.tmp)
        credential_store.save_api_key("test-api-key")
        import importlib
        import controller as ctrl_mod
        importlib.reload(ctrl_mod)
        from controller import Controller
        import requests.exceptions

        ctrl = Controller(_FakeRoot())

        with patch("rank_service.resolve_puuid",
                   side_effect=requests.exceptions.ConnectionError("no route to host")):
            with self.assertRaises(ValueError) as ctx:
                ctrl.add_account("Main", "player1", "password",
                                 riot_id="Main#EUW", region="EUW")
            # Converted to a German ValueError the dialog can surface inline
            self.assertIn("Netzwerkfehler", str(ctx.exception))

        # Account must NOT be in the list
        self.assertEqual(len(ctrl.state.accounts), 0)
        # Credential must be rolled back (no orphaned secret, T-02-08)
        self.assertFalse(bool(credential_store.get("player1")))

    def test_add_account_timeout_rolls_back_credential(self):
        """CR-02: a requests Timeout also rolls back the credential and converts to ValueError."""
        _make_tmp_config(self.tmp)
        credential_store.save_api_key("test-api-key")
        import importlib
        import controller as ctrl_mod
        importlib.reload(ctrl_mod)
        from controller import Controller
        import requests.exceptions

        ctrl = Controller(_FakeRoot())

        with patch("rank_service.resolve_puuid",
                   side_effect=requests.exceptions.Timeout("timed out")):
            with self.assertRaises(ValueError):
                ctrl.add_account("Main", "player1", "password",
                                 riot_id="Main#EUW", region="EUW")

        self.assertEqual(len(ctrl.state.accounts), 0)
        self.assertFalse(bool(credential_store.get("player1")))

    def test_add_account_whitespace_only_tag_rejected(self):
        """WR-05: a Riot-ID with a whitespace-only tag (e.g. 'Name# ') is rejected before any API call."""
        _make_tmp_config(self.tmp)
        credential_store.save_api_key("test-api-key")
        import importlib
        import controller as ctrl_mod
        importlib.reload(ctrl_mod)
        from controller import Controller

        ctrl = Controller(_FakeRoot())

        with patch("rank_service.resolve_puuid") as mock_resolve:
            with self.assertRaises(ValueError) as ctx:
                ctrl.add_account("Main", "player1", "password",
                                 riot_id="Name# ", region="EUW")
            self.assertIn("Format", str(ctx.exception))
            # API must never be called for a malformed Riot-ID
            mock_resolve.assert_not_called()

        # No account added and no orphaned credential
        self.assertEqual(len(ctrl.state.accounts), 0)
        self.assertFalse(bool(credential_store.get("player1")))

    def test_add_account_strips_riot_id_segments_before_resolve(self):
        """WR-05: leading/trailing spaces in the game name/tag are stripped before the API call."""
        _make_tmp_config(self.tmp)
        credential_store.save_api_key("test-api-key")
        import importlib
        import controller as ctrl_mod
        importlib.reload(ctrl_mod)
        from controller import Controller

        ctrl = Controller(_FakeRoot())

        with patch("rank_service.resolve_puuid", return_value="puuid-strip") as mock_resolve:
            ctrl.add_account("Main", "player1", "password",
                             riot_id="  Main  #  EUW  ", region="EUW")
            mock_resolve.assert_called_once_with(
                "Main", "EUW", "test-api-key", platform_id="EUW1"
            )

    def test_add_account_riot_id_no_api_key_stores_with_none_puuid(self):
        """With riot_id but no API key: account stored with puuid=None, no error."""
        _make_tmp_config(self.tmp)
        # Make sure no API key is set
        _fake_keyring.reset()
        import importlib
        import controller as ctrl_mod
        importlib.reload(ctrl_mod)
        from controller import Controller

        ctrl = Controller(_FakeRoot())
        ctrl.add_account("Main", "player1", "password",
                         riot_id="Main#EUW", region="EUW")
        acc = ctrl.state.accounts[0]
        self.assertIsNone(acc.puuid)
        self.assertEqual(acc.riot_id, "Main#EUW")
        self.assertEqual(acc.region, "EUW1")

    def test_add_account_riot_id_path_separator_rejected(self):
        """Riot-ID containing path separator '/' or '\\' is rejected (T-02-08)."""
        _make_tmp_config(self.tmp)
        _fake_keyring.reset()
        import importlib
        import controller as ctrl_mod
        importlib.reload(ctrl_mod)
        from controller import Controller

        ctrl = Controller(_FakeRoot())
        for bad_id in ["Main/EUW", "Main\\EUW", "../../etc/passwd#EUW"]:
            with self.assertRaises(ValueError):
                ctrl.add_account("Main", f"player_{bad_id[:3]}", "pass",
                                 riot_id=bad_id, region="EUW")

    def test_set_riot_id_updates_existing_account(self):
        """set_riot_id changes only riot_id/region/puuid on an existing account."""
        _make_tmp_config(self.tmp)
        _fake_keyring.reset()
        import importlib
        import controller as ctrl_mod
        importlib.reload(ctrl_mod)
        from controller import Controller

        ctrl = Controller(_FakeRoot())
        ctrl.add_account("Main", "player1", "password")
        # Now set riot_id with no API key → deferred
        ctrl.set_riot_id("player1", "Main#EUW", "EUW")
        acc = ctrl.state.accounts[0]
        self.assertEqual(acc.riot_id, "Main#EUW")
        self.assertEqual(acc.region, "EUW1")
        # username and has_snapshot must be unchanged
        self.assertEqual(acc.username, "player1")
        self.assertFalse(acc.has_snapshot)

    def test_api_key_never_in_accounts_json(self):
        """T-02-09: API key value must never appear in accounts.json."""
        _make_tmp_config(self.tmp)
        credential_store.save_api_key("SECRET-API-KEY-999")
        import importlib
        import controller as ctrl_mod
        importlib.reload(ctrl_mod)
        from controller import Controller

        ctrl = Controller(_FakeRoot())
        with patch("rank_service.resolve_puuid", return_value="puuid-001"):
            ctrl.add_account("Main", "player1", "password",
                             riot_id="Main#EUW", region="EUW")

        raw = config.ACCOUNTS_JSON.read_text(encoding="utf-8")
        self.assertNotIn("SECRET-API-KEY-999", raw)


# ===========================================================================
# Task 2 Tests: Controller rank-fetch orchestration + four refresh triggers
# ===========================================================================

class _SyncFakeRoot:
    """Fake root that records calls (v2.0: threading.Timer used, not root.after)."""

    def __init__(self):
        self.state = _FakeWindowState()
        self.after_calls: list = []

    def after(self, delay, fn, *args):
        # Legacy no-op; threading.Timer is now used instead
        self.after_calls.append((delay, fn))
        return "after_id"

    def after_cancel(self, after_id):
        pass

    def clipboard_clear(self):
        pass

    def clipboard_append(self, value):
        pass


def _make_ctrl_with_accounts(tmp: pathlib.Path, accounts: list[Account]):
    """Create a Controller pre-loaded with specific accounts."""
    _make_tmp_config(tmp)
    state = AppState(accounts=accounts, active_username=accounts[0].username if accounts else None)
    config.save_state(state)
    import importlib
    import controller as ctrl_mod
    importlib.reload(ctrl_mod)
    from controller import Controller
    fake_root = _SyncFakeRoot()
    ctrl = Controller(fake_root)
    return ctrl, fake_root


class TestTriggerRankRefresh(unittest.TestCase):
    """_trigger_rank_refresh: basic trigger logic."""

    def setUp(self):
        import tempfile
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        _fake_keyring.reset()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_trigger_no_api_key_starts_no_thread(self):
        """_trigger_rank_refresh with no API key starts zero threads."""
        accounts = [Account("p1", "Main", has_snapshot=True, puuid="puuid-abc")]
        ctrl, _ = _make_ctrl_with_accounts(self.tmp, accounts)
        # No API key stored
        with patch("threading.Thread") as mock_thread:
            ctrl._trigger_rank_refresh()
            mock_thread.assert_not_called()

    def test_trigger_with_api_key_starts_thread_for_account_with_puuid(self):
        """_trigger_rank_refresh with key + puuid account spawns a thread."""
        accounts = [Account("p1", "Main", has_snapshot=True, puuid="puuid-abc", region="EUW")]
        ctrl, _ = _make_ctrl_with_accounts(self.tmp, accounts)
        credential_store.save_api_key("my-api-key")
        with patch("threading.Thread") as mock_thread_cls:
            mock_t = MagicMock()
            mock_thread_cls.return_value = mock_t
            ctrl._trigger_rank_refresh()
            mock_thread_cls.assert_called_once()
            mock_t.start.assert_called_once()

    def test_trigger_skips_account_without_puuid(self):
        """_trigger_rank_refresh skips accounts without puuid (Pitfall 5 guard)."""
        accounts = [
            Account("p1", "Main", has_snapshot=True, puuid=None, riot_id="Main#EUW"),
        ]
        ctrl, _ = _make_ctrl_with_accounts(self.tmp, accounts)
        credential_store.save_api_key("my-api-key")
        with patch("threading.Thread") as mock_thread_cls:
            ctrl._trigger_rank_refresh()
            mock_thread_cls.assert_not_called()


class TestRankReady(unittest.TestCase):
    """_on_rank_ready: updates rank_cache, persists, notifies."""

    def setUp(self):
        import tempfile
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        _fake_keyring.reset()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_on_rank_ready_updates_cache_and_pushes_state(self):
        """_on_rank_ready sets rank_cache + rank_cache_ts and calls _push_state."""
        import time
        from unittest.mock import patch
        accounts = [Account("p1", "Main", has_snapshot=True, puuid="puuid-abc", region="EUW")]
        ctrl, _ = _make_ctrl_with_accounts(self.tmp, accounts)

        rank_info = RankInfo(
            solo=QueueRank("GOLD", "II", 47, 10, 8),
            flex=None,
            fetched_at=time.time(),
            stale=False,
        )
        with patch.object(ctrl, "_push_state") as mock_push:
            ctrl._on_rank_ready("p1", rank_info)

        acc = ctrl.state.accounts[0]
        self.assertIsNotNone(acc.rank_cache)
        self.assertIsNotNone(acc.rank_cache_ts)
        mock_push.assert_called_once()

        # Persisted to disk
        loaded = config.load_state()
        self.assertIsNotNone(loaded.accounts[0].rank_cache)

    def test_on_rank_ready_with_full_fetch_updates_both_queues(self):
        """_on_rank_ready serializes both solo and flex into rank_cache dict."""
        import time
        accounts = [Account("p1", "Main", has_snapshot=True, puuid="puuid-abc")]
        ctrl, _ = _make_ctrl_with_accounts(self.tmp, accounts)
        rank_info = RankInfo(
            solo=QueueRank("GOLD", "II", 47, 10, 8),
            flex=QueueRank("SILVER", "I", 20, 5, 5),
            fetched_at=time.time(),
            stale=False,
        )
        ctrl._on_rank_ready("p1", rank_info)
        acc = ctrl.state.accounts[0]
        self.assertIn("solo", acc.rank_cache)
        self.assertIn("flex", acc.rank_cache)
        self.assertIsNotNone(acc.rank_cache["solo"])
        self.assertIsNotNone(acc.rank_cache["flex"])


class TestRankError(unittest.TestCase):
    """_on_rank_error: stale marking, no-crash, keeps cache."""

    def setUp(self):
        import tempfile
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        _fake_keyring.reset()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_on_rank_error_with_cache_marks_stale(self):
        """_on_rank_error with existing cache sets rank_cache['stale'] = True."""
        accounts = [Account(
            "p1", "Main", has_snapshot=True, puuid="puuid-abc",
            rank_cache={"solo": None, "flex": None, "stale": False},
            rank_cache_ts=1000.0,
        )]
        ctrl, _ = _make_ctrl_with_accounts(self.tmp, accounts)
        ctrl._on_rank_error("p1", Exception("timeout"))
        acc = ctrl.state.accounts[0]
        # Cache should still be there (not wiped)
        self.assertIsNotNone(acc.rank_cache)
        # stale marker should be set
        self.assertTrue(acc.rank_cache.get("stale", False))

    def test_on_rank_error_without_cache_does_not_crash(self):
        """_on_rank_error with no existing cache does not crash and sets failure state."""
        accounts = [Account("p1", "Main", has_snapshot=True, puuid="puuid-abc")]
        ctrl, _ = _make_ctrl_with_accounts(self.tmp, accounts)
        # Should not raise
        ctrl._on_rank_error("p1", Exception("connection failed"))

    def test_on_rank_error_persists_stale_flag(self):
        """WR-06: _on_rank_error persists the stale flag so it survives a restart."""
        accounts = [Account(
            "p1", "Main", has_snapshot=True, puuid="puuid-abc",
            rank_cache={"solo": None, "flex": None, "stale": False},
            rank_cache_ts=1000.0,
        )]
        ctrl, _ = _make_ctrl_with_accounts(self.tmp, accounts)
        ctrl._on_rank_error("p1", Exception("timeout"))

        # Reload from disk — the on-disk cache must now carry stale=True
        loaded = config.load_state()
        self.assertTrue(loaded.accounts[0].rank_cache.get("stale", False))

    def test_on_rank_error_without_cache_persists_failure_marker(self):
        """WR-06: failure marker (no prior cache) is also persisted to disk."""
        accounts = [Account("p1", "Main", has_snapshot=True, puuid="puuid-abc")]
        ctrl, _ = _make_ctrl_with_accounts(self.tmp, accounts)
        ctrl._on_rank_error("p1", Exception("connection failed"))

        loaded = config.load_state()
        self.assertIsNotNone(loaded.accounts[0].rank_cache)
        self.assertTrue(loaded.accounts[0].rank_cache.get("failed", False))

    def test_on_rank_error_pushes_state(self):
        """_on_rank_error always calls _push_state regardless of cache state."""
        from unittest.mock import patch
        accounts = [Account("p1", "Main", has_snapshot=True, puuid="puuid-abc")]
        ctrl, _ = _make_ctrl_with_accounts(self.tmp, accounts)
        with patch.object(ctrl, "_push_state") as mock_push:
            ctrl._on_rank_error("p1", Exception("test"))
        mock_push.assert_called_once()

    def test_on_rank_error_401_posts_status_bar_message(self):
        """A 401 RiotAPIError posts the key-invalid message to the status bar."""
        from rank_service import RiotAPIError
        accounts = [Account("p1", "Main", has_snapshot=True, puuid="puuid-abc")]
        ctrl, _ = _make_ctrl_with_accounts(self.tmp, accounts)
        ctrl._on_rank_error("p1", RiotAPIError(401, "unauthorized"))
        # Status bar message should contain key-invalid text
        self.assertIn("API-Key", ctrl.state.status_message)


class TestFetchRankForAccount(unittest.TestCase):
    """_fetch_rank_for_account: background fetch posts result via root.after(0, ...)."""

    def setUp(self):
        import tempfile
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        _fake_keyring.reset()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fetch_success_posts_on_rank_ready(self):
        """Successful fetch posts _on_rank_ready via root.after(0, ...)."""
        accounts = [Account("p1", "Main", has_snapshot=True, puuid="puuid-abc", region="EUW")]
        ctrl, fake_root = _make_ctrl_with_accounts(self.tmp, accounts)

        mock_entries = [{"queueType": "RANKED_SOLO_5x5", "tier": "GOLD",
                         "rank": "II", "leaguePoints": 47, "wins": 10, "losses": 8}]
        with patch("rank_service.fetch_entries", return_value=mock_entries):
            # Call directly (synchronous test of the background worker)
            ctrl._fetch_rank_for_account("p1", "puuid-abc", "EUW", "api-key")

        # after(0, ...) was called (by the fake root it runs immediately)
        acc = ctrl.state.accounts[0]
        self.assertIsNotNone(acc.rank_cache)

    def test_fetch_error_posts_on_rank_error(self):
        """Failed fetch posts _on_rank_error via root.after(0, ...)."""
        accounts = [Account("p1", "Main", has_snapshot=True, puuid="puuid-abc", region="EUW")]
        ctrl, fake_root = _make_ctrl_with_accounts(self.tmp, accounts)

        with patch("rank_service.fetch_entries", side_effect=Exception("network error")):
            ctrl._fetch_rank_for_account("p1", "puuid-abc", "EUW", "api-key")
        # Should not raise; account has no cache but not crashed

    def test_fetch_404_falls_back_to_3call(self):
        """A 404 from fetch_entries falls back to 3-call chain."""
        from rank_service import RiotAPIError
        accounts = [Account("p1", "Main", has_snapshot=True, puuid="puuid-abc", region="EUW")]
        ctrl, fake_root = _make_ctrl_with_accounts(self.tmp, accounts)

        fallback_entries = [{"queueType": "RANKED_SOLO_5x5", "tier": "GOLD",
                             "rank": "II", "leaguePoints": 47, "wins": 10, "losses": 8}]
        with patch("rank_service.fetch_entries",
                   side_effect=RiotAPIError(404, "not found")) as mock_primary, \
             patch("rank_service.fetch_summoner_id", return_value="summoner-123") as mock_sid, \
             patch("rank_service.fetch_entries_by_summoner",
                   return_value=fallback_entries) as mock_fallback:
            ctrl._fetch_rank_for_account("p1", "puuid-abc", "EUW", "api-key")

        mock_sid.assert_called_once()
        mock_fallback.assert_called_once()
        acc = ctrl.state.accounts[0]
        self.assertIsNotNone(acc.rank_cache)

    def test_default_arg_capture_no_late_binding(self):
        """root.after(0, lambda ...) callbacks use default-arg capture, not late-binding."""
        # Test: all after(0, ...) lambdas with captured vars get the right account username
        accounts = [
            Account("p1", "Main", puuid="puuid-1", region="EUW"),
            Account("p2", "Smurf", puuid="puuid-2", region="EUNE"),
        ]
        ctrl, fake_root = _make_ctrl_with_accounts(self.tmp, accounts)
        credential_store.save_api_key("api-key")

        received_usernames = []

        def capture_on_rank_ready(username, rank_info):
            received_usernames.append(username)

        mock_entries = [{"queueType": "RANKED_SOLO_5x5", "tier": "GOLD",
                         "rank": "II", "leaguePoints": 0, "wins": 0, "losses": 0}]

        with patch("rank_service.fetch_entries", return_value=mock_entries), \
             patch.object(ctrl, "_on_rank_ready", side_effect=capture_on_rank_ready):
            ctrl._fetch_rank_for_account("p1", "puuid-1", "EUW", "api-key")
            ctrl._fetch_rank_for_account("p2", "puuid-2", "EUNE", "api-key")

        self.assertIn("p1", received_usernames)
        self.assertIn("p2", received_usernames)
        # Must not have the wrong username due to late binding
        self.assertNotEqual(received_usernames, ["p2", "p2"])


class TestSaveApiKeyTriggerRefresh(unittest.TestCase):
    """controller.save_api_key triggers _trigger_rank_refresh after storing key."""

    def setUp(self):
        import tempfile
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        _fake_keyring.reset()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_api_key_triggers_rank_refresh(self):
        """After save_api_key, _trigger_rank_refresh is called."""
        _make_tmp_config(self.tmp)
        import importlib
        import controller as ctrl_mod
        importlib.reload(ctrl_mod)
        from controller import Controller

        ctrl = Controller(_FakeRoot())
        # Plan 08-04 (D-03): save_api_key now live-validates before storing —
        # mock the network call so this test stays hermetic.
        with patch("rank_service.validate_api_key", return_value=True):
            with patch.object(ctrl, "_trigger_rank_refresh") as mock_refresh:
                ctrl.save_api_key("my-api-key-abc")
                mock_refresh.assert_called_once()


class TestRefreshRanksManual(unittest.TestCase):
    """controller.refresh_ranks (↻ button) delegates to _trigger_rank_refresh."""

    def setUp(self):
        import tempfile
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        _fake_keyring.reset()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_refresh_ranks_triggers_refresh(self):
        """refresh_ranks() must call _trigger_rank_refresh exactly once."""
        _make_tmp_config(self.tmp)
        import importlib
        import controller as ctrl_mod
        importlib.reload(ctrl_mod)
        from controller import Controller

        ctrl = Controller(_FakeRoot())
        with patch.object(ctrl, "_trigger_rank_refresh") as mock_refresh:
            ctrl.refresh_ranks()
            mock_refresh.assert_called_once_with()


class TestScheduleRankRefreshTimer(unittest.TestCase):
    """_schedule_rank_refresh_timer: calls trigger + schedules next run."""

    def setUp(self):
        import tempfile
        self.tmp = pathfile = pathlib.Path(tempfile.mkdtemp())
        _fake_keyring.reset()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_schedule_rank_refresh_timer_triggers_refresh(self):
        """_schedule_rank_refresh_timer calls _trigger_rank_refresh at least once."""
        _make_tmp_config(self.tmp)
        import importlib
        import controller as ctrl_mod
        importlib.reload(ctrl_mod)
        from controller import Controller

        class _RecordRoot:
            """Fake root (v2.0: threading.Timer used, not root.after)."""
            def __init__(self):
                self.state = _FakeWindowState()
                self.after_calls: list = []
            def after(self, delay, fn, *args):
                # Legacy no-op; threading.Timer is now used
                self.after_calls.append((delay, fn))
                return "id"
            def after_cancel(self, _): pass

        fake_root = _RecordRoot()
        ctrl = Controller(fake_root)

        with patch("threading.Timer") as mock_timer_cls, \
             patch.object(ctrl, "_trigger_rank_refresh") as mock_refresh:
            mock_timer_instance = mock_timer_cls.return_value
            ctrl._schedule_rank_refresh_timer()
            mock_refresh.assert_called_once()
            # threading.Timer should have been called to schedule the next run
            self.assertTrue(mock_timer_cls.called)

    def test_rank_refresh_interval_constant_exists(self):
        """RANK_REFRESH_INTERVAL_MS constant exists on Controller."""
        import importlib
        import controller as ctrl_mod
        importlib.reload(ctrl_mod)
        self.assertTrue(hasattr(ctrl_mod, "RANK_REFRESH_INTERVAL_MS") or
                        hasattr(ctrl_mod.Controller, "RANK_REFRESH_INTERVAL_MS") or
                        "RANK_REFRESH_INTERVAL_MS" in dir(ctrl_mod))


# ===========================================================================
# Edit-mode gap-fix tests: RiotIdDialog + AccountCard click binding
# ===========================================================================

class TestSetRiotIdEditPath(unittest.TestCase):
    """controller.set_riot_id: full edit-path coverage (edit-mode gap fix)."""

    def setUp(self):
        import tempfile
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        _fake_keyring.reset()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_ctrl(self):
        _make_tmp_config(self.tmp)
        import importlib
        import controller as ctrl_mod
        importlib.reload(ctrl_mod)
        from controller import Controller
        return Controller(_FakeRoot())

    def test_set_riot_id_with_api_key_resolves_puuid(self):
        """set_riot_id with a present API key resolves and stores the PUUID."""
        credential_store.save_api_key("my-key")
        ctrl = self._make_ctrl()
        ctrl.add_account("Main", "player1", "pass")

        with patch("rank_service.resolve_puuid", return_value="puuid-edit-001") as mock_r, \
             patch.object(ctrl, "_trigger_rank_refresh"):
            ctrl.set_riot_id("player1", "Main#EUW", "EUW")
            mock_r.assert_called_once_with("Main", "EUW", "my-key", platform_id="EUW1")

        acc = ctrl.state.accounts[0]
        self.assertEqual(acc.riot_id, "Main#EUW")
        self.assertEqual(acc.region, "EUW1")
        self.assertEqual(acc.puuid, "puuid-edit-001")
        # username and has_snapshot must be unchanged
        self.assertEqual(acc.username, "player1")
        self.assertFalse(acc.has_snapshot)

    def test_set_riot_id_404_raises_and_leaves_account_unchanged(self):
        """set_riot_id 404 raises ValueError; account riot_id/puuid stay as before."""
        credential_store.save_api_key("my-key")
        ctrl = self._make_ctrl()
        ctrl.add_account("Main", "player1", "pass")
        # Pre-set a riot_id so we can verify it is NOT changed on error
        ctrl.state.accounts[0].riot_id = "OldId#EUW"
        ctrl.state.accounts[0].region = "EUW"
        ctrl.state.accounts[0].puuid = "old-puuid"

        from rank_service import RiotAPIError
        with patch("rank_service.resolve_puuid", side_effect=RiotAPIError(404, "not found")):
            with self.assertRaises(ValueError) as ctx:
                ctrl.set_riot_id("player1", "NotExist#EUW", "EUW")
            self.assertIn("nicht gefunden", str(ctx.exception))

        # Account must retain old values (set_riot_id does NOT mutate on error)
        acc = ctrl.state.accounts[0]
        self.assertEqual(acc.riot_id, "OldId#EUW")
        self.assertEqual(acc.puuid, "old-puuid")

    def test_set_riot_id_deferred_no_api_key_stores_with_none_puuid(self):
        """set_riot_id with no API key stores riot_id with puuid=None (deferred path)."""
        _fake_keyring.reset()  # ensure no key
        ctrl = self._make_ctrl()
        ctrl.add_account("Main", "player1", "pass")

        ctrl.set_riot_id("player1", "Main#EUW", "EUW")
        acc = ctrl.state.accounts[0]
        self.assertEqual(acc.riot_id, "Main#EUW")
        self.assertIsNone(acc.puuid)

    def test_set_riot_id_pushes_state(self):
        """set_riot_id calls _push_state so the GUI refreshes after an edit."""
        from unittest.mock import patch
        _fake_keyring.reset()
        ctrl = self._make_ctrl()
        ctrl.add_account("Main", "player1", "pass")
        with patch.object(ctrl, "_push_state") as mock_push:
            ctrl.set_riot_id("player1", "Main#EUW", "EUW")
        mock_push.assert_called()

    def test_set_riot_id_path_separator_rejected(self):
        """set_riot_id rejects Riot-IDs with path separators (T-02-08)."""
        _fake_keyring.reset()
        ctrl = self._make_ctrl()
        ctrl.add_account("Main", "player1", "pass")
        for bad_id in ["Main/EUW", "Main\\EUW"]:
            with self.assertRaises(ValueError):
                ctrl.set_riot_id("player1", bad_id, "EUW")

    def test_set_riot_id_whitespace_only_tag_rejected(self):
        """WR-05: set_riot_id rejects a whitespace-only tag (e.g. 'Name# ')."""
        credential_store.save_api_key("my-key")
        ctrl = self._make_ctrl()
        ctrl.add_account("Main", "player1", "pass")
        with patch("rank_service.resolve_puuid") as mock_resolve:
            with self.assertRaises(ValueError) as ctx:
                ctrl.set_riot_id("player1", "Name# ", "EUW")
            self.assertIn("Format", str(ctx.exception))
            mock_resolve.assert_not_called()

    def test_set_riot_id_network_error_raises_valueerror(self):
        """CR-02: a network error during set_riot_id resolve converts to a German ValueError."""
        credential_store.save_api_key("my-key")
        ctrl = self._make_ctrl()
        ctrl.add_account("Main", "player1", "pass")
        import requests.exceptions
        with patch("rank_service.resolve_puuid",
                   side_effect=requests.exceptions.ConnectionError("boom")):
            with self.assertRaises(ValueError) as ctx:
                ctrl.set_riot_id("player1", "Main#EUW", "EUW")
            self.assertIn("Netzwerkfehler", str(ctx.exception))

    def test_set_riot_id_triggers_rank_refresh(self):
        """set_riot_id calls _trigger_rank_refresh after a successful edit (gap fix D-23).

        Mirrors test_save_api_key_triggers_rank_refresh: verifies that editing a
        Riot-ID causes an immediate rank refresh so the card does not stay on
        'Rang: lädt...' until the app is restarted.
        """
        credential_store.save_api_key("my-key")
        ctrl = self._make_ctrl()
        ctrl.add_account("Main", "player1", "pass")

        with patch("rank_service.resolve_puuid", return_value="puuid-refresh-001"), \
             patch.object(ctrl, "_trigger_rank_refresh") as mock_refresh:
            ctrl.set_riot_id("player1", "Main#EUW", "EUW")
            mock_refresh.assert_called_once()


# NOTE: TestRiotIdDialogImport removed in Phase 4 Plan 05 — the 5 customtkinter
# GUI modules (main_window, account_card, add_account_dialog, confirm_dialog,
# riot_id_dialog) were deleted as part of the customtkinter → pywebview migration.
# The tests that validated those modules are no longer applicable.


if __name__ == "__main__":
    unittest.main()
