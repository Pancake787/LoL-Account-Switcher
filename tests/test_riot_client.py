"""Tests for riot_client.py.

psutil is mocked via patch.object(riot_client, "psutil", ...) in each test so
tests never touch real OS processes and the import order does not matter.
Uses a tmp directory for SESSION_FILE and snapshot dirs.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import MagicMock, patch

import os as _os
if "LOCALAPPDATA" not in _os.environ:
    _os.environ["LOCALAPPDATA"] = str(pathlib.Path.home())

import riot_client  # noqa: E402


# ---------------------------------------------------------------------------
# Fake psutil classes — never touch real processes
# ---------------------------------------------------------------------------

class _FakeProcess:
    """A fake psutil process with .info dict and .kill() tracking."""

    def __init__(self, name: str, pid: int = 1):
        self.info = {"name": name, "pid": pid}
        self._alive = True
        self.killed = False

    def kill(self) -> None:
        self.killed = True
        self._alive = False

    def name(self) -> str:
        return self.info["name"]


class _FakeNoSuchProcess(Exception):
    pass


class _FakeAccessDenied(Exception):
    pass


class _FakePsutil:
    """Minimal psutil stub used as a replacement for the psutil module."""

    NoSuchProcess = _FakeNoSuchProcess
    AccessDenied = _FakeAccessDenied

    def __init__(self, procs: list[_FakeProcess] | None = None):
        self._procs: list[_FakeProcess] = procs or []

    def set_procs(self, procs: list[_FakeProcess]) -> None:
        self._procs = procs

    def process_iter(self, attrs=None):  # noqa: ARG002
        return [p for p in self._procs if p._alive]


# ---------------------------------------------------------------------------
# Base test class — redirects SESSION_FILE + snapshot dir to tmp;
# patches riot_client.psutil per test so import order is irrelevant
# ---------------------------------------------------------------------------

class _RiotClientTestBase(unittest.TestCase):
    """Base class: tmp filesystem + psutil patch per test."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._tmp = pathlib.Path(self._tmpdir.name)
        self._session_file = self._tmp / "RiotGamesPrivateSettings.yaml"
        self._sessions_dir = self._tmp / "sessions"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)

        # Per-test fake psutil (replaces riot_client.psutil attribute)
        self._fake_psutil = _FakePsutil()

        self._patcher_session = patch.object(riot_client, "SESSION_FILE", self._session_file)
        self._patcher_snap = patch(
            "riot_client._snapshot_path",
            lambda username: self._sessions_dir / username / "RiotGamesPrivateSettings.yaml",
        )
        self._patcher_psutil = patch.object(riot_client, "psutil", self._fake_psutil)

        self._patcher_session.start()
        self._patcher_snap.start()
        self._patcher_psutil.start()

    def tearDown(self):
        self._patcher_psutil.stop()
        self._patcher_session.stop()
        self._patcher_snap.stop()
        self._tmpdir.cleanup()

    def _set_procs(self, procs: list[_FakeProcess]) -> None:
        """Update the fake psutil process list for this test."""
        self._fake_psutil.set_procs(procs)


# ---------------------------------------------------------------------------
# Test Suite: Constants (no psutil needed)
# ---------------------------------------------------------------------------

class TestRiotClientConstants(unittest.TestCase):
    """Verify MODULE-LEVEL constants are correct."""

    def test_session_file_ends_with_correct_path(self):
        """SESSION_FILE must end with Riot Client/Data/RiotGamesPrivateSettings.yaml."""
        path_str = str(riot_client.SESSION_FILE).replace("\\", "/")
        self.assertTrue(
            path_str.endswith("Riot Client/Data/RiotGamesPrivateSettings.yaml"),
            f"SESSION_FILE does not end with correct path: {path_str}",
        )

    def test_session_file_not_lockfile(self):
        """SESSION_FILE must NOT be the lockfile."""
        self.assertNotIn("lockfile", str(riot_client.SESSION_FILE).lower())

    def test_session_file_not_riot_client_settings(self):
        """SESSION_FILE must NOT be RiotClientSettings.yaml (wrong file)."""
        self.assertNotIn("RiotClientSettings.yaml", str(riot_client.SESSION_FILE))

    def test_riot_kill_order_starts_with_leaf_renderer(self):
        """RIOT_KILL_ORDER must start with the leaf renderer (LeagueClientUxRender.exe)."""
        self.assertTrue(
            riot_client.RIOT_KILL_ORDER[0].lower().startswith("leagueclientuxrender"),
            f"RIOT_KILL_ORDER[0] should be LeagueClientUxRender.exe, got: {riot_client.RIOT_KILL_ORDER[0]}",
        )

    def test_riot_kill_order_ends_with_services(self):
        """RIOT_KILL_ORDER must end with RiotClientServices.exe (parent)."""
        self.assertTrue(
            riot_client.RIOT_KILL_ORDER[-1].lower().startswith("riotclientservices"),
            f"RIOT_KILL_ORDER[-1] should be RiotClientServices.exe, got: {riot_client.RIOT_KILL_ORDER[-1]}",
        )

    def test_riot_kill_order_has_at_least_six_entries(self):
        """RIOT_KILL_ORDER must include at least the 6 standard processes."""
        self.assertGreaterEqual(len(riot_client.RIOT_KILL_ORDER), 6)

    def test_game_process_constant(self):
        """GAME_PROCESS must be 'league of legends.exe' (lowercase)."""
        self.assertEqual(riot_client.GAME_PROCESS.lower(), "league of legends.exe")


# ---------------------------------------------------------------------------
# Test Suite: is_game_running
# ---------------------------------------------------------------------------

class TestIsGameRunning(_RiotClientTestBase):
    """Tests for riot_client.is_game_running()."""

    def test_returns_true_when_game_process_running(self):
        """is_game_running returns True if 'League of Legends.exe' is in process list."""
        self._set_procs([_FakeProcess("League of Legends.exe")])
        self.assertTrue(riot_client.is_game_running())

    def test_case_insensitive_detection(self):
        """is_game_running is case-insensitive (matches 'league of legends.exe')."""
        self._set_procs([_FakeProcess("league of legends.exe")])
        self.assertTrue(riot_client.is_game_running())

    def test_returns_false_when_game_not_running(self):
        """is_game_running returns False when no game process is present."""
        self._set_procs([
            _FakeProcess("RiotClientServices.exe"),
            _FakeProcess("LeagueClient.exe"),
        ])
        self.assertFalse(riot_client.is_game_running())

    def test_returns_false_on_empty_process_list(self):
        """is_game_running returns False when process list is empty."""
        self._set_procs([])
        self.assertFalse(riot_client.is_game_running())

    def test_does_not_match_riot_client_processes(self):
        """is_game_running does NOT match Riot Client processes (D-08)."""
        self._set_procs([
            _FakeProcess("RiotClientServices.exe"),
            _FakeProcess("RiotClientUx.exe"),
            _FakeProcess("LeagueClientUx.exe"),
        ])
        self.assertFalse(riot_client.is_game_running())


# ---------------------------------------------------------------------------
# Test Suite: is_client_running (D-12/STATUS-01)
# ---------------------------------------------------------------------------

class TestIsClientRunning(_RiotClientTestBase):
    """Tests for riot_client.is_client_running() — mirrors TestIsGameRunning."""

    def test_returns_true_when_riot_client_services_running(self):
        """is_client_running returns True if 'RiotClientServices.exe' is in process list."""
        self._set_procs([_FakeProcess("RiotClientServices.exe")])
        self.assertTrue(riot_client.is_client_running())

    def test_case_insensitive_detection(self):
        """is_client_running is case-insensitive (matches 'riotclientservices.exe')."""
        self._set_procs([_FakeProcess("riotclientservices.exe")])
        self.assertTrue(riot_client.is_client_running())

    def test_returns_false_when_client_not_running(self):
        """is_client_running returns False when RiotClientServices.exe is absent."""
        self._set_procs([
            _FakeProcess("League of Legends.exe"),
            _FakeProcess("LeagueClient.exe"),
        ])
        self.assertFalse(riot_client.is_client_running())

    def test_returns_false_on_empty_process_list(self):
        """is_client_running returns False when process list is empty."""
        self._set_procs([])
        self.assertFalse(riot_client.is_client_running())

    def test_does_not_match_other_riot_processes(self):
        """is_client_running does NOT match sibling Riot/League processes."""
        self._set_procs([
            _FakeProcess("RiotClientUx.exe"),
            _FakeProcess("LeagueClientUx.exe"),
            _FakeProcess("vgtray.exe"),
        ])
        self.assertFalse(riot_client.is_client_running())


# ---------------------------------------------------------------------------
# Test Suite: stop()
# ---------------------------------------------------------------------------

class TestStop(_RiotClientTestBase):
    """Tests for riot_client.stop()."""

    def test_stop_kills_all_riot_processes_and_returns_true(self):
        """stop() kills all RIOT_KILL_ORDER processes and returns True."""
        procs = [
            _FakeProcess("RiotClientServices.exe", pid=100),
            _FakeProcess("RiotClientUx.exe", pid=101),
            _FakeProcess("LeagueClient.exe", pid=102),
        ]
        self._set_procs(procs)
        result = riot_client.stop(timeout=5.0)
        self.assertTrue(result)
        self.assertTrue(all(p.killed for p in procs))

    def test_stop_returns_false_on_timeout(self):
        """stop() returns False if processes do not die before timeout."""
        immortal = _FakeProcess("RiotClientServices.exe", pid=200)
        # Override kill() so the process stays alive
        immortal.kill = lambda: None  # does NOT set _alive = False

        self._set_procs([immortal])
        result = riot_client.stop(timeout=0.3)
        self.assertFalse(result)

    def test_stop_does_not_rely_on_fixed_sleep(self):
        """stop() uses polling, not a fixed pre-swap sleep."""
        proc = _FakeProcess("RiotClientServices.exe", pid=300)
        self._set_procs([proc])

        start = time.monotonic()
        result = riot_client.stop(timeout=5.0)
        elapsed = time.monotonic() - start

        self.assertTrue(result)
        self.assertLess(elapsed, 3.0, "stop() appears to use a fixed sleep (too slow)")

    def test_stop_handles_no_such_process_gracefully(self):
        """stop() swallows NoSuchProcess exceptions during kill."""
        proc = _FakeProcess("LeagueClient.exe", pid=400)

        def _dying_kill():
            proc._alive = False
            raise _FakeNoSuchProcess("already dead")

        proc.kill = _dying_kill
        self._set_procs([proc])
        result = riot_client.stop(timeout=5.0)
        self.assertTrue(result)

    def test_stop_ignores_non_riot_processes(self):
        """stop() does not kill processes not in RIOT_KILL_ORDER."""
        other = _FakeProcess("chrome.exe", pid=999)
        riot = _FakeProcess("RiotClientServices.exe", pid=500)
        self._set_procs([other, riot])

        riot_client.stop(timeout=5.0)
        self.assertFalse(other.killed, "stop() must not kill non-Riot processes")


# ---------------------------------------------------------------------------
# Test Suite: swap_session
# ---------------------------------------------------------------------------

class TestSwapSession(_RiotClientTestBase):
    """Tests for riot_client.swap_session()."""

    def test_raises_file_not_found_when_no_snapshot(self):
        """swap_session raises FileNotFoundError when no snapshot exists (first-login signal)."""
        with self.assertRaises(FileNotFoundError):
            riot_client.swap_session("riotuser1")

    def test_atomic_replace_when_snapshot_exists(self):
        """swap_session copies snapshot to SESSION_FILE atomically via os.replace."""
        snap_dir = self._sessions_dir / "riotuser1"
        snap_dir.mkdir(parents=True, exist_ok=True)
        snap_file = snap_dir / "RiotGamesPrivateSettings.yaml"
        snap_file.write_text("token: test_rso_token_abc")

        riot_client.swap_session("riotuser1")

        self.assertTrue(self._session_file.exists())
        content = self._session_file.read_text()
        self.assertIn("test_rso_token_abc", content)

    def test_swap_preserves_snapshot_file(self):
        """swap_session does not delete the snapshot file after swapping."""
        snap_dir = self._sessions_dir / "riotuser1"
        snap_dir.mkdir(parents=True, exist_ok=True)
        snap_file = snap_dir / "RiotGamesPrivateSettings.yaml"
        snap_file.write_text("token: preserved")

        riot_client.swap_session("riotuser1")
        self.assertTrue(snap_file.exists(), "Snapshot file must not be deleted after swap")

    def test_swap_does_not_leave_partial_file_on_permission_error(self):
        """swap_session uses staging + os.replace so SESSION_FILE is never partially written."""
        snap_dir = self._sessions_dir / "riotuser1"
        snap_dir.mkdir(parents=True, exist_ok=True)
        snap_file = snap_dir / "RiotGamesPrivateSettings.yaml"
        snap_file.write_text("token: atomic_check")

        riot_client.swap_session("riotuser1")

        content = self._session_file.read_text()
        self.assertIn("atomic_check", content)


class TestSnapshotExists(_RiotClientTestBase):
    """Tests for riot_client.snapshot_exists() — pre-flight guard before stop()."""

    def test_false_when_no_snapshot(self):
        """No snapshot file -> False (core.perform_switch must abort before stop())."""
        self.assertFalse(riot_client.snapshot_exists("riotuser1"))

    def test_true_when_snapshot_present(self):
        """Snapshot file present -> True (switch may proceed)."""
        snap_dir = self._sessions_dir / "riotuser1"
        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / "RiotGamesPrivateSettings.yaml").write_text("token: x")
        self.assertTrue(riot_client.snapshot_exists("riotuser1"))

    def test_agrees_with_swap_session(self):
        """snapshot_exists() must agree with swap_session(): if it returns False,
        swap_session() raises FileNotFoundError (the two share the same file)."""
        self.assertFalse(riot_client.snapshot_exists("ghost"))
        with self.assertRaises(FileNotFoundError):
            riot_client.swap_session("ghost")


# ---------------------------------------------------------------------------
# Helpers shared by _looks_logged_in and save_snapshot_now tests
# ---------------------------------------------------------------------------

# A minimal token string that satisfies _looks_logged_in (>= 100 chars from base64url alphabet)
_FAKE_TOKEN = "A" * 120  # 120 contiguous base64url chars — looks like an RSO token run

def _logged_in_content(extra: str = "") -> str:
    """Return plausible logged-in YAML content containing a fake RSO token run."""
    return (
        "global:\n"
        f"  rso-auth-tokens.access_token: \"{_FAKE_TOKEN}\"\n"
        f"  note: {extra or 'logged-in'}\n"
    )

def _logged_out_content() -> str:
    """Return a plausible logged-out / client-initialized YAML with NO token run."""
    # All values are short — no run of 100+ base64url chars exists.
    return (
        "global:\n"
        "  locale: de_DE\n"
        "  region: EUW1\n"
        "  patchline: live\n"
        "  product: league_of_legends\n"
    )


# ---------------------------------------------------------------------------
# Test Suite: _looks_logged_in
# ---------------------------------------------------------------------------

class TestLooksLoggedIn(unittest.TestCase):
    """Unit tests for the riot_client._looks_logged_in() helper."""

    def test_returns_true_for_long_token_run(self):
        """_looks_logged_in returns True when content contains 100+ base64url chars."""
        self.assertTrue(riot_client._looks_logged_in(_FAKE_TOKEN))

    def test_returns_false_for_short_values_only(self):
        """_looks_logged_in returns False for logged-out config (no long token run)."""
        self.assertFalse(riot_client._looks_logged_in(_logged_out_content()))

    def test_returns_false_for_empty_string(self):
        """_looks_logged_in returns False for empty content."""
        self.assertFalse(riot_client._looks_logged_in(""))

    def test_exactly_99_chars_does_not_match(self):
        """_looks_logged_in returns False for a 99-char run (boundary check)."""
        self.assertFalse(riot_client._looks_logged_in("A" * 99))

    def test_exactly_100_chars_matches(self):
        """_looks_logged_in returns True for a 100-char run (boundary check)."""
        self.assertTrue(riot_client._looks_logged_in("A" * 100))

    def test_dot_dash_underscore_counted(self):
        """_looks_logged_in counts '.', '-', '_' as part of the base64url alphabet."""
        # JWT segments are separated by '.' — a segment boundary would NOT break a 100-char run
        # that uses the full alphabet including those chars.
        run = ("A" * 33 + "-" + "B" * 33 + "_" + "C" * 33)  # 101 chars total, all base64url
        self.assertTrue(riot_client._looks_logged_in(run))


# ---------------------------------------------------------------------------
# Test Suite: save_snapshot_now
# ---------------------------------------------------------------------------

class TestSaveSnapshotNow(_RiotClientTestBase):
    """Tests for riot_client.save_snapshot_now() — D-04 manual confirm flow."""

    def test_returns_false_when_session_file_absent(self):
        """save_snapshot_now returns False when SESSION_FILE does not exist."""
        # Ensure session file is absent
        if self._session_file.exists():
            self._session_file.unlink()
        result = riot_client.save_snapshot_now("riotuser1")
        self.assertFalse(result)

    def test_returns_false_when_session_file_too_small(self):
        """save_snapshot_now returns False when SESSION_FILE is < 100 bytes."""
        self._session_file.write_text("short")  # well under 100 bytes
        result = riot_client.save_snapshot_now("riotuser1")
        self.assertFalse(result)

    def test_returns_false_and_does_not_copy_when_no_token(self):
        """save_snapshot_now returns False and does not copy when content has no token run.

        Covers the "clicked too early / not yet logged in" case: the file is >= 100 bytes
        and exists, but contains only short YAML values with no 100+ char base64url run.
        """
        # Write logged-out content that is large enough (repeat to exceed 100 bytes)
        self._session_file.write_text(_logged_out_content() * 5)
        result = riot_client.save_snapshot_now("riotuser1")
        self.assertFalse(result)

        # Snapshot directory must NOT have been created / file must NOT exist
        snap = self._sessions_dir / "riotuser1" / "RiotGamesPrivateSettings.yaml"
        self.assertFalse(snap.exists(), "Snapshot must not be created for a logged-out file")

    def test_returns_true_and_copies_when_token_present(self):
        """save_snapshot_now returns True and copies file to snapshot dir when logged in.

        The live session file contains a 100+ char base64url token run — simulates a
        file written by Riot after the user logs in.
        """
        self._session_file.write_text(_logged_in_content("fresh-login"))
        result = riot_client.save_snapshot_now("riotuser1")
        self.assertTrue(result)

        snap = self._sessions_dir / "riotuser1" / "RiotGamesPrivateSettings.yaml"
        self.assertTrue(snap.exists(), "Snapshot must be created when login detected")
        self.assertIn(_FAKE_TOKEN, snap.read_text())

    def test_snapshot_is_written_to_per_user_dir(self):
        """save_snapshot_now writes the snapshot under sessions/<username>/."""
        self._session_file.write_text(_logged_in_content("user2-data"))
        riot_client.save_snapshot_now("riotuser2")

        snap = self._sessions_dir / "riotuser2" / "RiotGamesPrivateSettings.yaml"
        self.assertTrue(snap.exists())
        self.assertIn(_FAKE_TOKEN, snap.read_text())

    def test_returns_false_for_99_char_run(self):
        """save_snapshot_now returns False for a 99-char token run (boundary, < 100 chars)."""
        content = "x: " + ("A" * 99) + "\n"
        # Pad to exceed 100 bytes in total file size
        content = content + ("b: padding\n" * 5)
        self._session_file.write_text(content)
        self.assertFalse(riot_client.save_snapshot_now("riotuser1"))

    def test_returns_true_for_exactly_100_char_run(self):
        """save_snapshot_now returns True for exactly a 100-char token run (boundary)."""
        content = "x: " + ("A" * 100) + "\n"
        content = content + ("b: padding\n" * 5)
        self._session_file.write_text(content)
        self.assertTrue(riot_client.save_snapshot_now("riotuser1"))


# ---------------------------------------------------------------------------
# Test Suite: clear_session
# ---------------------------------------------------------------------------

class TestClearSession(_RiotClientTestBase):
    """Tests for riot_client.clear_session()."""

    def test_removes_existing_session_file(self):
        """clear_session() deletes the live SESSION_FILE when it exists."""
        self._session_file.write_text("some: content")
        self.assertTrue(self._session_file.exists())
        riot_client.clear_session()
        self.assertFalse(self._session_file.exists())

    def test_no_op_when_session_file_absent(self):
        """clear_session() does not raise when SESSION_FILE does not exist."""
        self.assertFalse(self._session_file.exists())
        riot_client.clear_session()  # must not raise
        self.assertFalse(self._session_file.exists())

    def test_retries_on_permission_error_then_succeeds(self):
        """clear_session() retries up to 3× on PermissionError, succeeds on second attempt."""
        import os as _os_mod
        self._session_file.write_text("token: data")

        call_count = [0]
        original_remove = _os_mod.remove

        def _flaky_remove(path):
            call_count[0] += 1
            if call_count[0] == 1:
                raise PermissionError("handle held")
            original_remove(path)

        with patch.object(riot_client.os, "remove", side_effect=_flaky_remove), \
             patch.object(riot_client.time, "sleep"):  # skip real sleep in tests
            riot_client.clear_session()

        self.assertEqual(call_count[0], 2)
        self.assertFalse(self._session_file.exists())

    def test_raises_permission_error_after_all_retries_exhausted(self):
        """clear_session() re-raises PermissionError after 3 failed attempts."""
        self._session_file.write_text("token: data")

        def _always_perm(path):
            raise PermissionError("persistent lock")

        with patch.object(riot_client.os, "remove", side_effect=_always_perm), \
             patch.object(riot_client.time, "sleep"):
            with self.assertRaises(PermissionError):
                riot_client.clear_session()


# ---------------------------------------------------------------------------
# Test Suite: refresh_snapshot
# ---------------------------------------------------------------------------

class TestRefreshSnapshot(_RiotClientTestBase):
    """Tests for riot_client.refresh_snapshot()."""

    def test_copies_live_to_snapshot_when_logged_in(self):
        """refresh_snapshot() copies SESSION_FILE → snapshot and returns True when logged in."""
        live_content = _logged_in_content("refresh-test")
        self._session_file.write_text(live_content)

        result = riot_client.refresh_snapshot("riotuser1")

        self.assertTrue(result)
        snap = self._sessions_dir / "riotuser1" / "RiotGamesPrivateSettings.yaml"
        self.assertTrue(snap.exists())
        self.assertIn(_FAKE_TOKEN, snap.read_text())

    def test_overwrites_existing_snapshot_with_fresh_content(self):
        """refresh_snapshot() overwrites the old snapshot with the new live content."""
        # Pre-existing snapshot with old content
        snap_dir = self._sessions_dir / "riotuser1"
        snap_dir.mkdir(parents=True, exist_ok=True)
        snap_file = snap_dir / "RiotGamesPrivateSettings.yaml"
        snap_file.write_text("old: snapshot content without token")

        # Live file with a fresh token
        new_content = _logged_in_content("new-token")
        self._session_file.write_text(new_content)

        result = riot_client.refresh_snapshot("riotuser1")

        self.assertTrue(result)
        refreshed = snap_file.read_text()
        self.assertIn(_FAKE_TOKEN, refreshed)
        self.assertNotIn("old: snapshot content without token", refreshed)

    def test_returns_false_when_session_file_absent(self):
        """refresh_snapshot() returns False (no raise) when SESSION_FILE does not exist."""
        self.assertFalse(self._session_file.exists())
        result = riot_client.refresh_snapshot("riotuser1")
        self.assertFalse(result)

    def test_does_not_overwrite_snapshot_when_live_not_logged_in(self):
        """refresh_snapshot() returns False and leaves existing snapshot untouched when live is not logged in."""
        # Pre-existing snapshot
        snap_dir = self._sessions_dir / "riotuser1"
        snap_dir.mkdir(parents=True, exist_ok=True)
        snap_file = snap_dir / "RiotGamesPrivateSettings.yaml"
        original_snap = "original: snapshot"
        snap_file.write_text(original_snap)

        # Live file without a token (logged-out state — pad to >= 100 bytes)
        self._session_file.write_text(_logged_out_content() * 5)

        result = riot_client.refresh_snapshot("riotuser1")

        self.assertFalse(result)
        # Snapshot must be unchanged
        self.assertEqual(snap_file.read_text(), original_snap)

    def test_returns_false_when_live_file_too_small(self):
        """refresh_snapshot() returns False when SESSION_FILE is < 100 bytes."""
        self._session_file.write_text("short")
        result = riot_client.refresh_snapshot("riotuser1")
        self.assertFalse(result)

    def test_snapshot_written_to_per_user_dir(self):
        """refresh_snapshot() writes to the correct per-user snapshot directory."""
        self._session_file.write_text(_logged_in_content("user2"))
        riot_client.refresh_snapshot("riotuser2")
        snap = self._sessions_dir / "riotuser2" / "RiotGamesPrivateSettings.yaml"
        self.assertTrue(snap.exists())


# ---------------------------------------------------------------------------
# Test Suite: is_snapshot_stale (D-21, shared predicate with D-20's guard)
# ---------------------------------------------------------------------------

class TestIsSnapshotStale(_RiotClientTestBase):
    """Tests for riot_client.is_snapshot_stale() — the D-21 UI-hint predicate."""

    def test_true_for_present_but_tokenless_snapshot(self):
        """is_snapshot_stale returns True for a present-but-tokenless/small snapshot."""
        snap_dir = self._sessions_dir / "riotuser1"
        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / "RiotGamesPrivateSettings.yaml").write_text(_logged_out_content() * 5)
        self.assertTrue(riot_client.is_snapshot_stale("riotuser1"))

    def test_false_for_valid_snapshot(self):
        """is_snapshot_stale returns False for a valid, token-bearing snapshot."""
        snap_dir = self._sessions_dir / "riotuser1"
        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / "RiotGamesPrivateSettings.yaml").write_text(_logged_in_content("valid"))
        self.assertFalse(riot_client.is_snapshot_stale("riotuser1"))

    def test_false_when_no_snapshot_exists(self):
        """is_snapshot_stale returns False when no snapshot file exists at all."""
        self.assertFalse(riot_client.is_snapshot_stale("ghost"))

    def test_false_for_too_small_snapshot(self):
        """is_snapshot_stale returns True for a snapshot under 100 bytes (shares the size gate)."""
        snap_dir = self._sessions_dir / "riotuser1"
        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / "RiotGamesPrivateSettings.yaml").write_text("short")
        self.assertTrue(riot_client.is_snapshot_stale("riotuser1"))


# ---------------------------------------------------------------------------
# Test Suite: _file_looks_logged_in (shared predicate, D-20/D-21)
# ---------------------------------------------------------------------------

class TestFileLooksLoggedIn(_RiotClientTestBase):
    """Tests for the shared riot_client._file_looks_logged_in() predicate."""

    def test_false_when_file_absent(self):
        missing = self._tmp / "does_not_exist.yaml"
        self.assertFalse(riot_client._file_looks_logged_in(missing))

    def test_false_when_too_small(self):
        f = self._tmp / "small.yaml"
        f.write_text("short")
        self.assertFalse(riot_client._file_looks_logged_in(f))

    def test_true_when_logged_in_content(self):
        f = self._tmp / "live.yaml"
        f.write_text(_logged_in_content("check"))
        self.assertTrue(riot_client._file_looks_logged_in(f))

    def test_false_when_logged_out_content(self):
        f = self._tmp / "live.yaml"
        f.write_text(_logged_out_content() * 5)
        self.assertFalse(riot_client._file_looks_logged_in(f))


# ---------------------------------------------------------------------------
# Test Suite: start
# ---------------------------------------------------------------------------

class TestStart(unittest.TestCase):
    """Tests for riot_client.start() — D-13 windowless launch hardening."""

    def test_start_launches_with_create_no_window(self):
        """start() must pass creationflags=subprocess.CREATE_NO_WINDOW, not 0."""
        import subprocess

        with patch.object(riot_client.subprocess, "Popen") as mock_popen:
            riot_client.start(pathlib.Path("RiotClientServices.exe"))

        mock_popen.assert_called_once()
        _, kwargs = mock_popen.call_args
        self.assertEqual(kwargs.get("creationflags"), subprocess.CREATE_NO_WINDOW)

    def test_start_preserves_launch_args_and_no_shell(self):
        """start() still passes the launch-product/patchline args and never shell=True."""
        with patch.object(riot_client.subprocess, "Popen") as mock_popen:
            riot_client.start(pathlib.Path("RiotClientServices.exe"))

        args, kwargs = mock_popen.call_args
        self.assertIn("--launch-product=league_of_legends", args[0])
        self.assertIn("--launch-patchline=live", args[0])
        self.assertNotIn("shell", kwargs)


if __name__ == "__main__":
    unittest.main()
