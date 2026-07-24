"""test_core.py — Branch-Coverage fuer core.perform_switch und core.SwitchResult.

Testet alle 8 Behaviour-Cases aus Plan 03-01 Task 2:
  - BLOCKED: League of Legends.exe laeuft -> hard block, kein stop()
  - STOP_FAILED: stop() -> False -> kein swap_session
  - NO_SNAPSHOT: swap_session -> FileNotFoundError -> NO_SNAPSHOT (D-34)
  - RIOT_NOT_FOUND: find_riot_client_exe -> None -> RIOT_NOT_FOUND, kein start()
  - SUCCESS: happy path -> SUCCESS, start() aufgerufen
  - refresh-best-effort: active != target mit Snapshot, refresh_snapshot raises -> trotzdem SUCCESS
  - ERROR: unerwartete Exception -> ERROR (kein Re-raise)
  - no-persist: save_state niemals aufgerufen (D-30b)

Hermetisch: alle riot_client.* und config.load_state via unittest.mock.patch.
"""
from __future__ import annotations

import pathlib
import tempfile
import unittest
from unittest.mock import MagicMock, patch, call

from models import Account, AppState
import core
import riot_client
from core import SwitchResult


def _make_state(
    *,
    accounts: list[Account] | None = None,
    active_username: str | None = None,
) -> AppState:
    """Erstelle ein einfaches AppState-Fixture fuer Tests."""
    return AppState(
        accounts=accounts or [],
        active_username=active_username,
    )


class TestPerformSwitchBlocked(unittest.TestCase):
    """BLOCKED: League laeuft -> sofortiger Hard-Block, stop() wird NICHT aufgerufen."""

    def test_blocked_when_game_running(self):
        state = _make_state()
        with patch("core.riot_client.is_game_running", return_value=True), \
             patch("core.riot_client.stop") as mock_stop, \
             patch("core.config.load_state", return_value=state):
            result = core.perform_switch("testuser")
        self.assertEqual(result, SwitchResult.BLOCKED)
        mock_stop.assert_not_called()


class TestPerformSwitchStopFailed(unittest.TestCase):
    """STOP_FAILED: stop() gibt False -> kein swap_session danach."""

    def test_stop_failed_when_stop_returns_false(self):
        state = _make_state()
        with patch("core.riot_client.is_game_running", return_value=False), \
             patch("core.riot_client.snapshot_exists", return_value=True), \
             patch("core.riot_client.stop", return_value=False) as mock_stop, \
             patch("core.riot_client.swap_session") as mock_swap, \
             patch("core.config.load_state", return_value=state):
            result = core.perform_switch("testuser")
        self.assertEqual(result, SwitchResult.STOP_FAILED)
        mock_stop.assert_called_once_with(timeout=10.0)
        mock_swap.assert_not_called()


class TestPerformSwitchNoSnapshot(unittest.TestCase):
    """NO_SNAPSHOT (D-34): kein Snapshot -> NO_SNAPSHOT, OHNE die laufende Session zu beenden."""

    def test_no_snapshot_does_not_stop_session(self):
        """Kein Snapshot -> NO_SNAPSHOT VOR stop(): laufende Session bleibt unangetastet."""
        state = _make_state()
        with patch("core.riot_client.is_game_running", return_value=False), \
             patch("core.riot_client.snapshot_exists", return_value=False), \
             patch("core.riot_client.stop") as mock_stop, \
             patch("core.riot_client.swap_session") as mock_swap, \
             patch("core.config.load_state", return_value=state):
            result = core.perform_switch("testuser")
        self.assertEqual(result, SwitchResult.NO_SNAPSHOT)
        # Kritisch (User-Anforderung): ohne Snapshot wird NICHT beendet/getauscht.
        mock_stop.assert_not_called()
        mock_swap.assert_not_called()

    def test_no_snapshot_on_file_not_found_race_fallback(self):
        """Belt-and-suspenders: Snapshot verschwindet zwischen Check und swap_session
        (Race) -> swap_session FileNotFoundError mappt weiterhin auf NO_SNAPSHOT."""
        state = _make_state()
        with patch("core.riot_client.is_game_running", return_value=False), \
             patch("core.riot_client.snapshot_exists", return_value=True), \
             patch("core.riot_client.stop", return_value=True), \
             patch("core.riot_client.swap_session", side_effect=FileNotFoundError("kein Snapshot")), \
             patch("core.config.load_state", return_value=state):
            result = core.perform_switch("testuser")
        self.assertEqual(result, SwitchResult.NO_SNAPSHOT)


class TestPerformSwitchRiotNotFound(unittest.TestCase):
    """RIOT_NOT_FOUND: find_riot_client_exe() gibt None -> RIOT_NOT_FOUND, start() nicht aufgerufen."""

    def test_riot_not_found_when_exe_is_none(self):
        state = _make_state()
        with patch("core.riot_client.is_game_running", return_value=False), \
             patch("core.riot_client.snapshot_exists", return_value=True), \
             patch("core.riot_client.stop", return_value=True), \
             patch("core.riot_client.swap_session"), \
             patch("core.riot_client.find_riot_client_exe", return_value=None), \
             patch("core.riot_client.start") as mock_start, \
             patch("core.config.load_state", return_value=state):
            result = core.perform_switch("testuser")
        self.assertEqual(result, SwitchResult.RIOT_NOT_FOUND)
        mock_start.assert_not_called()


class TestPerformSwitchSuccess(unittest.TestCase):
    """SUCCESS: Happy Path -> SUCCESS, start() mit der gefundenen exe aufgerufen."""

    def test_success_all_steps_ok(self):
        state = _make_state()
        fake_exe = pathlib.Path("C:/Riot Games/RiotClientServices.exe")
        with patch("core.riot_client.is_game_running", return_value=False), \
             patch("core.riot_client.snapshot_exists", return_value=True), \
             patch("core.riot_client.stop", return_value=True), \
             patch("core.riot_client.swap_session"), \
             patch("core.riot_client.find_riot_client_exe", return_value=fake_exe), \
             patch("core.riot_client.start") as mock_start, \
             patch("core.config.load_state", return_value=state):
            result = core.perform_switch("targetuser")
        self.assertEqual(result, SwitchResult.SUCCESS)
        mock_start.assert_called_once_with(fake_exe)


class TestPerformSwitchRefreshBestEffort(unittest.TestCase):
    """refresh-best-effort: active != target mit Snapshot, refresh_snapshot raises -> trotzdem SUCCESS."""

    def test_refresh_exception_does_not_abort_switch(self):
        # Aktiver Account "currentuser" hat einen Snapshot
        accounts = [
            Account(username="currentuser", display_name="Current", has_snapshot=True),
            Account(username="targetuser", display_name="Target", has_snapshot=True),
        ]
        state = _make_state(accounts=accounts, active_username="currentuser")
        fake_exe = pathlib.Path("C:/Riot Games/RiotClientServices.exe")
        with patch("core.riot_client.is_game_running", return_value=False), \
             patch("core.riot_client.snapshot_exists", return_value=True), \
             patch("core.riot_client.stop", return_value=True), \
             patch("core.riot_client.swap_session"), \
             patch("core.riot_client.find_riot_client_exe", return_value=fake_exe), \
             patch("core.riot_client.start"), \
             patch("core.riot_client.refresh_snapshot", side_effect=OSError("disk error")) as mock_refresh, \
             patch("core.config.load_state", return_value=state):
            result = core.perform_switch("targetuser")
        # refresh_snapshot wurde aufgerufen
        mock_refresh.assert_called_once_with("currentuser")
        # Exception bricht den Switch NICHT ab
        self.assertEqual(result, SwitchResult.SUCCESS)

    def test_refresh_called_only_when_active_has_snapshot(self):
        """refresh_snapshot wird nur aufgerufen wenn active != target und active hat Snapshot."""
        accounts = [
            Account(username="currentuser", display_name="Current", has_snapshot=False),
            Account(username="targetuser", display_name="Target", has_snapshot=False),
        ]
        state = _make_state(accounts=accounts, active_username="currentuser")
        fake_exe = pathlib.Path("C:/Riot Games/RiotClientServices.exe")
        with patch("core.riot_client.is_game_running", return_value=False), \
             patch("core.riot_client.snapshot_exists", return_value=True), \
             patch("core.riot_client.stop", return_value=True), \
             patch("core.riot_client.swap_session"), \
             patch("core.riot_client.find_riot_client_exe", return_value=fake_exe), \
             patch("core.riot_client.start"), \
             patch("core.riot_client.refresh_snapshot") as mock_refresh, \
             patch("core.config.load_state", return_value=state):
            result = core.perform_switch("targetuser")
        # has_snapshot=False -> refresh_snapshot wird NICHT aufgerufen
        mock_refresh.assert_not_called()
        self.assertEqual(result, SwitchResult.SUCCESS)


class TestPerformSwitchRefreshOutgoingRegression(unittest.TestCase):
    """D-20 regression: the REAL riot_client.refresh_snapshot() guard, exercised in
    perform_switch's Step 0, must not overwrite a good outgoing snapshot with an
    empty/tokenless live SESSION_FILE.

    Unlike TestPerformSwitchRefreshBestEffort (which patches
    ``core.riot_client.refresh_snapshot`` entirely), this test leaves
    ``refresh_snapshot`` UNPATCHED so its real exists/size>=100/_looks_logged_in
    guard actually runs against real files on disk — reproducing the empirical
    "Smurf" corruption case from 04-04-SUMMARY.md (a 484-byte tokenless snapshot
    overwriting a valid 2495-byte one).

    Documented blind spot (Pitfall 2 / RESEARCH.md): this guard only catches a
    LOCALLY empty/tokenless/small live session file. A well-formed, full-size
    RSO token that Riot has silently invalidated server-side would still pass
    this guard's size + regex checks unchanged — that undetectable case is
    exactly what recapture_session() (D-19) exists to fix, NOT this guard.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._tmp = pathlib.Path(self._tmpdir.name)
        self._session_file = self._tmp / "RiotGamesPrivateSettings.yaml"
        self._sessions_dir = self._tmp / "sessions"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)

        # Patch the REAL riot_client module attributes (core.py's `import riot_client`
        # refers to this same module object) so the real refresh_snapshot() reads/
        # writes real temp files instead of the actual %LOCALAPPDATA%/%APPDATA% paths.
        self._patcher_session = patch.object(riot_client, "SESSION_FILE", self._session_file)
        self._patcher_snap = patch(
            "riot_client._snapshot_path",
            lambda username: self._sessions_dir / username / "RiotGamesPrivateSettings.yaml",
        )
        self._patcher_session.start()
        self._patcher_snap.start()

    def tearDown(self):
        self._patcher_session.stop()
        self._patcher_snap.stop()
        self._tmpdir.cleanup()

    def test_real_refresh_snapshot_does_not_overwrite_good_snapshot_with_tokenless_live_file(self):
        """A tiny/tokenless live SESSION_FILE must NOT overwrite a good outgoing snapshot."""
        # Good pre-existing snapshot for the outgoing ("currentuser") account —
        # large, token-bearing (mirrors the empirical 2495-byte valid case).
        outgoing_snap_dir = self._sessions_dir / "currentuser"
        outgoing_snap_dir.mkdir(parents=True, exist_ok=True)
        outgoing_snap_file = outgoing_snap_dir / "RiotGamesPrivateSettings.yaml"
        good_token = "A" * 2200
        good_content = f'global:\n  rso-auth-tokens.access_token: "{good_token}"\n'
        outgoing_snap_file.write_text(good_content)

        # Tiny/tokenless live session file — mirrors the empirical 484-byte
        # "Smurf" corruption case (padded to exceed 100 bytes, no long token run).
        tokenless_content = "global:\n  locale: de_DE\n  region: EUW1\n" * 12
        self._session_file.write_text(tokenless_content)

        accounts = [
            Account(username="currentuser", display_name="Current", has_snapshot=True),
            Account(username="targetuser", display_name="Target", has_snapshot=True),
        ]
        state = _make_state(accounts=accounts, active_username="currentuser")
        fake_exe = pathlib.Path("C:/Riot Games/RiotClientServices.exe")

        # riot_client.refresh_snapshot is the REAL function (not patched) — only
        # the non-FS-safety parts of the sequence are mocked.
        with patch("core.riot_client.snapshot_exists", return_value=True), \
             patch("core.riot_client.is_game_running", return_value=False), \
             patch("core.riot_client.stop", return_value=True), \
             patch("core.riot_client.swap_session"), \
             patch("core.riot_client.find_riot_client_exe", return_value=fake_exe), \
             patch("core.riot_client.start"), \
             patch("core.config.load_state", return_value=state):
            result = core.perform_switch("targetuser")

        self.assertEqual(result, SwitchResult.SUCCESS)
        # The good snapshot must be UNCHANGED — the real refresh_snapshot() guard
        # (exists -> size>=100 -> _looks_logged_in) rejected the tokenless live file.
        refreshed_content = outgoing_snap_file.read_text()
        self.assertEqual(refreshed_content, good_content)
        self.assertIn(good_token, refreshed_content)


class TestPerformSwitchError(unittest.TestCase):
    """ERROR: Unerwartete Exception -> SwitchResult.ERROR (kein Re-raise)."""

    def test_error_on_unexpected_exception(self):
        state = _make_state()
        with patch("core.riot_client.is_game_running", return_value=False), \
             patch("core.riot_client.snapshot_exists", return_value=True), \
             patch("core.riot_client.stop", return_value=True), \
             patch("core.riot_client.swap_session"), \
             patch("core.riot_client.find_riot_client_exe", side_effect=RuntimeError("unexpected")), \
             patch("core.config.load_state", return_value=state):
            result = core.perform_switch("testuser")
        self.assertEqual(result, SwitchResult.ERROR)

    def test_no_reraise_on_exception(self):
        """perform_switch darf keine Exception weiterleiten (sicher fuer CLI + GUI)."""
        state = _make_state()
        with patch("core.riot_client.is_game_running", return_value=False), \
             patch("core.riot_client.snapshot_exists", return_value=True), \
             patch("core.riot_client.stop", side_effect=Exception("catastrophic")), \
             patch("core.config.load_state", return_value=state):
            # Muss ohne Exception laufen
            result = core.perform_switch("testuser")
        self.assertEqual(result, SwitchResult.ERROR)


class TestPerformSwitchNoPersist(unittest.TestCase):
    """no-persist: config.save_state wird in KEINEM Branch aufgerufen (D-30b)."""

    def _run_switch_all_branches(self, mock_save_state):
        """Hilfsmethode: fuehrt perform_switch fuer alle relevanten Branches aus."""
        state = _make_state()
        fake_exe = pathlib.Path("C:/Riot Games/RiotClientServices.exe")

        # BLOCKED
        with patch("core.riot_client.is_game_running", return_value=True), \
             patch("core.config.load_state", return_value=state):
            core.perform_switch("u1")

        # STOP_FAILED
        with patch("core.riot_client.is_game_running", return_value=False), \
             patch("core.riot_client.snapshot_exists", return_value=True), \
             patch("core.riot_client.stop", return_value=False), \
             patch("core.config.load_state", return_value=state):
            core.perform_switch("u1")

        # NO_SNAPSHOT (pre-guard: kein Snapshot)
        with patch("core.riot_client.is_game_running", return_value=False), \
             patch("core.riot_client.snapshot_exists", return_value=False), \
             patch("core.config.load_state", return_value=state):
            core.perform_switch("u1")

        # NO_SNAPSHOT (race-fallback: swap_session FileNotFoundError)
        with patch("core.riot_client.is_game_running", return_value=False), \
             patch("core.riot_client.snapshot_exists", return_value=True), \
             patch("core.riot_client.stop", return_value=True), \
             patch("core.riot_client.swap_session", side_effect=FileNotFoundError()), \
             patch("core.config.load_state", return_value=state):
            core.perform_switch("u1")

        # SUCCESS
        with patch("core.riot_client.is_game_running", return_value=False), \
             patch("core.riot_client.snapshot_exists", return_value=True), \
             patch("core.riot_client.stop", return_value=True), \
             patch("core.riot_client.swap_session"), \
             patch("core.riot_client.find_riot_client_exe", return_value=fake_exe), \
             patch("core.riot_client.start"), \
             patch("core.config.load_state", return_value=state):
            core.perform_switch("u1")

    def test_save_state_never_called_in_any_branch(self):
        """D-30b: perform_switch persistiert active_username NICHT."""
        with patch("core.config.save_state") as mock_save_state:
            self._run_switch_all_branches(mock_save_state)
            mock_save_state.assert_not_called()


class TestCoreImportGuard(unittest.TestCase):
    """Verifikation: core.py importiert kein tkinter/customtkinter/threading etc."""

    def test_no_forbidden_imports_in_core(self):
        """AST-basierte Pruefung: keine verbotenen Imports auf Modulebene."""
        import ast
        import os
        # Finde core.py relativ zum Testverzeichnis
        core_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "core.py")
        src = open(core_path, encoding="utf-8").read()
        tree = ast.parse(src)
        mods = (
            [n.names[0].name for n in ast.walk(tree) if isinstance(n, ast.Import)]
            + [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        )
        bad = [m for m in mods if m and (
            "tkinter" in m
            or m in ("threading", "rank_service", "credential_store", "customtkinter")
        )]
        self.assertEqual(bad, [], f"Verbotene Imports in core.py: {bad}")

    def test_perform_switch_importable_without_tkinter_side_effect(self):
        """perform_switch und SwitchResult muessen ohne tkinter-Seiteneffekt importierbar sein."""
        from core import perform_switch, SwitchResult
        self.assertIsNotNone(perform_switch)
        self.assertIsNotNone(SwitchResult)


class TestValidateUsername(unittest.TestCase):
    """CR-01 / T-03-01: validate_username weist Pfadtrenner-/Traversal-usernames ab."""

    def test_accepts_plain_username(self):
        core.validate_username("alice")  # darf nicht werfen

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            core.validate_username("")

    def test_rejects_whitespace_padding(self):
        with self.assertRaises(ValueError):
            core.validate_username(" alice ")

    def test_rejects_forward_slash(self):
        with self.assertRaises(ValueError):
            core.validate_username("a/b")

    def test_rejects_backslash(self):
        with self.assertRaises(ValueError):
            core.validate_username("a\\b")

    def test_rejects_parent_ref(self):
        with self.assertRaises(ValueError):
            core.validate_username("..")

    def test_rejects_backslash_traversal(self):
        with self.assertRaises(ValueError):
            core.validate_username("..\\..\\Windows\\Temp\\x")

    def test_rejects_absolute_drive_path(self):
        with self.assertRaises(ValueError):
            core.validate_username("C:\\Windows\\evil")

    def test_rejects_nul(self):
        with self.assertRaises(ValueError):
            core.validate_username("a\0b")


class TestPerformSwitchRejectsTraversal(unittest.TestCase):
    """CR-01: ein traversierender username erreicht NIE swap_session und liefert ERROR."""

    def test_traversing_username_returns_error_without_swap(self):
        state = _make_state()
        with patch("core.riot_client.is_game_running", return_value=False), \
             patch("core.riot_client.stop", return_value=True) as mock_stop, \
             patch("core.riot_client.swap_session") as mock_swap, \
             patch("core.config.load_state", return_value=state):
            result = core.perform_switch("..\\..\\Windows\\Temp\\x")
        self.assertEqual(result, SwitchResult.ERROR)
        mock_swap.assert_not_called()
        mock_stop.assert_not_called()

    def test_absolute_path_username_returns_error_without_swap(self):
        state = _make_state()
        with patch("core.riot_client.is_game_running", return_value=False), \
             patch("core.riot_client.swap_session") as mock_swap, \
             patch("core.config.load_state", return_value=state):
            result = core.perform_switch("C:\\Windows\\evil")
        self.assertEqual(result, SwitchResult.ERROR)
        mock_swap.assert_not_called()


if __name__ == "__main__":
    unittest.main()
