"""Tests for config.py's silent EUW/EUNE -> canonical region migration (REGION-02, D-12)
and the five Phase-8 app-wide settings fields on AppState (foundation for
ONBOARD-02/03/04).

Unit tests only — no live Riot API calls (rank_service.regional_host_for is
pure lookup logic, safe to call directly here to prove end-to-end migration).
"""
from __future__ import annotations

import pathlib
import tempfile
import unittest

import config
import rank_service
from models import Account, AppState


def _acc(username: str, display: str, **kwargs) -> Account:
    return Account(username=username, display_name=display, has_snapshot=True, **kwargs)


class _ConfigTmpDirMixin:
    """Redirects config's file paths onto a fresh temp dir per test (same
    pattern as tests/test_config_recovery.py)."""

    def setUp(self) -> None:
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self._orig_app_dir = config.APP_DIR
        self._orig_accounts = config.ACCOUNTS_JSON
        self._orig_sessions = config.SESSIONS_DIR
        config.APP_DIR = self.tmp
        config.ACCOUNTS_JSON = self.tmp / "accounts.json"
        config.SESSIONS_DIR = self.tmp / "sessions"

    def tearDown(self) -> None:
        config.APP_DIR = self._orig_app_dir
        config.ACCOUNTS_JSON = self._orig_accounts
        config.SESSIONS_DIR = self._orig_sessions


# ---------------------------------------------------------------------------
# Test: _normalize_region (pure function, no file I/O)
# ---------------------------------------------------------------------------


class TestNormalizeRegion(unittest.TestCase):
    def test_legacy_euw_migrates_to_euw1(self):
        self.assertEqual(config._normalize_region("EUW"), "EUW1")

    def test_legacy_eune_migrates_to_eun1(self):
        """Pitfall 2: EUNE != EUN1 — a generic .upper() pass would miss this."""
        self.assertEqual(config._normalize_region("EUNE"), "EUN1")

    def test_canonical_euw1_passthrough(self):
        self.assertEqual(config._normalize_region("EUW1"), "EUW1")

    def test_canonical_na1_passthrough(self):
        self.assertEqual(config._normalize_region("NA1"), "NA1")

    def test_lowercase_legacy_input_still_migrates(self):
        self.assertEqual(config._normalize_region("euw"), "EUW1")
        self.assertEqual(config._normalize_region("eune"), "EUN1")


# ---------------------------------------------------------------------------
# Test: fixture-based load migration (REGION-02, D-12) — literal legacy JSON
# ---------------------------------------------------------------------------


class TestRegionMigrationOnLoad(_ConfigTmpDirMixin, unittest.TestCase):
    def _write_raw_accounts_json(self, raw: str) -> None:
        config.ensure_dirs()
        config.ACCOUNTS_JSON.write_text(raw, encoding="utf-8")

    def test_euw_account_migrates_to_euw1(self):
        """Fixture accounts.json with the literal legacy string 'EUW' (Pitfall 2)."""
        self._write_raw_accounts_json(
            '{"accounts": [{"username": "u1", "display_name": "One", '
            '"region": "EUW"}], "active_username": null}'
        )
        state = config.load_state()
        self.assertEqual(state.accounts[0].region, "EUW1")

    def test_eune_account_migrates_to_eun1(self):
        """Fixture accounts.json with the literal legacy string 'EUNE' (Pitfall 2)."""
        self._write_raw_accounts_json(
            '{"accounts": [{"username": "u2", "display_name": "Two", '
            '"region": "EUNE"}], "active_username": null}'
        )
        state = config.load_state()
        self.assertEqual(state.accounts[0].region, "EUN1")

    def test_migrated_euw_resolves_to_europe_cluster(self):
        """End-to-end migration proof (RESEARCH.md Runtime State Inventory)."""
        self._write_raw_accounts_json(
            '{"accounts": [{"username": "u1", "display_name": "One", '
            '"region": "EUW"}], "active_username": null}'
        )
        state = config.load_state()
        host = rank_service.regional_host_for(state.accounts[0].region)
        self.assertEqual(host, "europe.api.riotgames.com")

    def test_migrated_eune_resolves_to_europe_cluster(self):
        """End-to-end migration proof (RESEARCH.md Runtime State Inventory)."""
        self._write_raw_accounts_json(
            '{"accounts": [{"username": "u2", "display_name": "Two", '
            '"region": "EUNE"}], "active_username": null}'
        )
        state = config.load_state()
        host = rank_service.regional_host_for(state.accounts[0].region)
        self.assertEqual(host, "europe.api.riotgames.com")

    def test_already_canonical_region_is_unaffected(self):
        """A post-Phase-8 account already storing 'NA1' passes through unchanged."""
        self._write_raw_accounts_json(
            '{"accounts": [{"username": "u3", "display_name": "Three", '
            '"region": "NA1"}], "active_username": null}'
        )
        state = config.load_state()
        self.assertEqual(state.accounts[0].region, "NA1")

    def test_migration_is_silent_no_extra_state(self):
        """D-12: migration produces no dialog/flag — just the normalized field."""
        self._write_raw_accounts_json(
            '{"accounts": [{"username": "u1", "display_name": "One", '
            '"region": "EUW"}], "active_username": null}'
        )
        state = config.load_state()
        # No new top-level "migration" flags leak into AppState — region is
        # simply the canonical value, and everything else loads normally.
        self.assertEqual(state.accounts[0].username, "u1")
        self.assertIsNone(state.active_username)


# ---------------------------------------------------------------------------
# Test: five app-wide settings fields — save/load round-trip
# ---------------------------------------------------------------------------


class TestSettingsFieldsRoundTrip(_ConfigTmpDirMixin, unittest.TestCase):
    def test_full_roundtrip_preserves_all_five_values(self):
        state = AppState(
            accounts=[_acc("u1", "One")],
            language="de",
            update_check_enabled=False,
            dismissed_update_version="v2.2.0",
            disable_gpu=False,
            update_last_checked=123.0,
        )
        config.save_state(state)
        loaded = config.load_state()

        self.assertEqual(loaded.language, "de")
        self.assertEqual(loaded.update_check_enabled, False)
        self.assertEqual(loaded.dismissed_update_version, "v2.2.0")
        self.assertEqual(loaded.disable_gpu, False)
        self.assertEqual(loaded.update_last_checked, 123.0)

    def test_english_language_roundtrip(self):
        state = AppState(accounts=[_acc("u1", "One")], language="en")
        config.save_state(state)
        loaded = config.load_state()
        self.assertEqual(loaded.language, "en")

    def test_update_check_enabled_true_roundtrip(self):
        state = AppState(
            accounts=[_acc("u1", "One")],
            update_check_enabled=True,
            disable_gpu=True,
        )
        config.save_state(state)
        loaded = config.load_state()
        self.assertTrue(loaded.update_check_enabled)
        self.assertTrue(loaded.disable_gpu)


# ---------------------------------------------------------------------------
# Test: backward compatibility — pre-Phase-8 accounts.json lacks all 5 keys
# ---------------------------------------------------------------------------


class TestSettingsFieldsBackwardCompat(_ConfigTmpDirMixin, unittest.TestCase):
    def test_missing_keys_return_defaults_without_raising(self):
        """A pre-Phase-8 accounts.json (no language/update_check_enabled/
        dismissed_update_version/disable_gpu/update_last_checked keys at all)
        must load without error and produce the documented defaults."""
        config.ensure_dirs()
        config.ACCOUNTS_JSON.write_text(
            '{"accounts": [{"username": "u1", "display_name": "One"}], '
            '"active_username": null}',
            encoding="utf-8",
        )
        loaded = config.load_state()

        self.assertIsNone(loaded.language)
        self.assertTrue(loaded.update_check_enabled)
        self.assertIsNone(loaded.dismissed_update_version)
        self.assertTrue(loaded.disable_gpu)
        self.assertEqual(loaded.update_last_checked, 0.0)

    def test_missing_file_returns_defaults(self):
        """First run, no accounts.json at all — same defaults, no crash."""
        loaded = config.load_state()
        self.assertIsNone(loaded.language)
        self.assertTrue(loaded.update_check_enabled)
        self.assertIsNone(loaded.dismissed_update_version)
        self.assertTrue(loaded.disable_gpu)
        self.assertEqual(loaded.update_last_checked, 0.0)


if __name__ == "__main__":
    unittest.main()
