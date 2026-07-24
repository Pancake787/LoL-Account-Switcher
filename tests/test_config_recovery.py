"""Tests for config.save_state/load_state data-safety hardening.

Covers the corruption + recovery path added after a concurrent-write incident:
a fixed temp filename let two app instances clobber the same temp file and
leave trailing garbage in accounts.json, and load_state then silently returned
an empty AppState (risking a wipe on the next save).

Hardening under test:
- unique per-write temp file (mkstemp) — no leftover fixed-name temp
- rolling backup (accounts.json.bak) of the last known-good file
- auto-recovery on load: corrupt primary is quarantined (.corrupt) and the
  backup is restored + returned
"""
from __future__ import annotations

import pathlib
import tempfile
import unittest

import config
from models import Account, AppState


def _acc(username: str, display: str) -> Account:
    return Account(username=username, display_name=display, has_snapshot=True)


class TestConfigRecovery(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        # Redirect all config paths onto the tmp dir (same pattern as the other
        # config-touching tests). Backup/corrupt/temp paths are derived from
        # ACCOUNTS_JSON at call time, so overriding it is sufficient.
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

    # --- basic round-trip still works -------------------------------------

    def test_save_load_roundtrip(self) -> None:
        state = AppState(accounts=[_acc("u1", "One"), _acc("u2", "Two")],
                         active_username="u1")
        config.save_state(state)
        loaded = config.load_state()
        self.assertEqual([a.username for a in loaded.accounts], ["u1", "u2"])
        self.assertEqual(loaded.active_username, "u1")

    # --- unique temp: no leftover fixed-name temp -------------------------

    def test_save_leaves_no_fixed_temp_file(self) -> None:
        config.save_state(AppState(accounts=[_acc("u1", "One")]))
        # The old bug used a fixed "accounts.json.tmp"; it must not survive.
        self.assertFalse((self.tmp / "accounts.json.tmp").exists())
        # And no stray mkstemp temp files should remain either.
        leftover = list(self.tmp.glob("accounts.*.tmp"))
        self.assertEqual(leftover, [], f"stray temp files: {leftover}")

    # --- rolling backup ---------------------------------------------------

    def test_second_save_creates_backup_of_previous_good_file(self) -> None:
        config.save_state(AppState(accounts=[_acc("u1", "One")]))
        # Second save should back up the (good) first version first.
        config.save_state(AppState(accounts=[_acc("u1", "One"), _acc("u2", "Two")]))
        bak = self.tmp / "accounts.json.bak"
        self.assertTrue(bak.exists(), "expected rolling backup accounts.json.bak")
        # Backup holds the PREVIOUS state (single account).
        import json
        prev = json.loads(bak.read_text(encoding="utf-8"))
        self.assertEqual([a["username"] for a in prev["accounts"]], ["u1"])

    def test_backup_not_overwritten_by_corrupt_current(self) -> None:
        # First save → good file. Second save → good .bak (state A).
        config.save_state(AppState(accounts=[_acc("good", "Good")]))
        config.save_state(AppState(accounts=[_acc("good", "Good"), _acc("b", "B")]))
        # Corrupt the live file, then save again — the backup must NOT be
        # overwritten with the corrupt content.
        config.ACCOUNTS_JSON.write_text("{ this is not json", encoding="utf-8")
        config.save_state(AppState(accounts=[_acc("new", "New")]))
        import json
        bak = json.loads((self.tmp / "accounts.json.bak").read_text(encoding="utf-8"))
        # Backup still holds the last KNOWN-GOOD (state A: good + b), not corrupt.
        self.assertIn("accounts", bak)
        self.assertTrue(all("username" in a for a in bak["accounts"]))

    # --- auto-recovery on load -------------------------------------------

    def test_corrupt_primary_recovers_from_backup(self) -> None:
        # Establish a good file + a good backup.
        config.save_state(AppState(accounts=[_acc("keep", "Keep")]))
        config.save_state(AppState(accounts=[_acc("keep", "Keep"), _acc("also", "Also")]))
        # Simulate the exact incident: valid JSON + trailing garbage.
        good = config.ACCOUNTS_JSON.read_text(encoding="utf-8")
        config.ACCOUNTS_JSON.write_text(good + '0"\n}', encoding="utf-8")

        loaded = config.load_state()

        # Accounts recovered from backup (the previous known-good save).
        self.assertEqual([a.username for a in loaded.accounts], ["keep"])
        # Corrupt file quarantined, live file restored, backup intact.
        self.assertTrue((self.tmp / "accounts.json.corrupt").exists())
        self.assertTrue(config.ACCOUNTS_JSON.exists())
        self.assertIsNotNone(config._try_load(config.ACCOUNTS_JSON))

    def test_corrupt_primary_no_backup_returns_empty_and_quarantines(self) -> None:
        config.ACCOUNTS_JSON.write_text('{"accounts": [garbage', encoding="utf-8")
        loaded = config.load_state()
        self.assertEqual(loaded.accounts, [])
        # Corrupt file preserved (not silently deleted) for manual recovery.
        self.assertTrue((self.tmp / "accounts.json.corrupt").exists())

    def test_missing_file_returns_empty(self) -> None:
        self.assertFalse(config.ACCOUNTS_JSON.exists())
        loaded = config.load_state()
        self.assertEqual(loaded.accounts, [])
        self.assertIsNone(loaded.active_username)


if __name__ == "__main__":
    unittest.main()
