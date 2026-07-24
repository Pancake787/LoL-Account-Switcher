"""riot_client.py — Riot/League process management and session file swap.

Implements:
- RIOT_KILL_ORDER: leaf-to-parent process kill order (CLAUDE.md Critical Notes)
- SESSION_FILE: path to RiotGamesPrivateSettings.yaml (NOT lockfile, NOT RiotClientSettings.yaml)
- GAME_PROCESS: the real game executable for the match guard (D-08/SWITCH-02)
- is_game_running(): psutil check for GAME_PROCESS (D-07/D-08)
- stop(): kill all RIOT_KILL_ORDER processes + poll until dead (no fixed sleep, CRIT-1/COMMON-1)
- find_riot_client_exe(): Registry lookup + hardcoded fallback paths
- swap_session(): staging + os.replace (atomic), raises FileNotFoundError for first-login flow
- start(): subprocess.Popen non-blocking client restart
- save_snapshot_now(): synchronous on-demand snapshot capture (manual confirm flow, D-04)
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import time
import winreg
from typing import Optional

import psutil

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Kill order — leaf renderer processes first, then their parents (CLAUDE.md Critical Notes).
#: This matches the Riot/League process tree exactly so child processes are terminated
#: before their parents, avoiding orphan handles on the session file.
RIOT_KILL_ORDER: list[str] = [
    "LeagueClientUxRender.exe",
    "LeagueClientUx.exe",
    "LeagueClient.exe",
    "RiotClientUxRender.exe",
    "RiotClientUx.exe",
    # vgtray.exe (Vanguard System Tray) is killed before RiotClientServices.exe;
    # it can hold file handles on the Riot Data folder and cause a PermissionError
    # during os.replace() (Pitfall 6).  Killing it first avoids the race.
    "vgtray.exe",
    "RiotClientServices.exe",
]

#: The target session file — contains the RSO token used for auto-login.
#: Path derived from %LOCALAPPDATA% — never hardcoded (CRIT-3).
#: NOT lockfile (runtime only) and NOT RiotClientSettings.yaml (wrong file) — CLAUDE.md.
#: Filename empirically verified on real system 2026-06-06: Riot renamed
#: RiotClientPrivateSettings.yaml → RiotGamesPrivateSettings.yaml.
SESSION_FILE: pathlib.Path = (
    pathlib.Path(os.environ["LOCALAPPDATA"])
    / "Riot Games"
    / "Riot Client"
    / "Data"
    / "RiotGamesPrivateSettings.yaml"
)

#: Exact process name for the real League of Legends game (D-08 / SWITCH-02).
#: Used by is_game_running() to distinguish a live match from mere client/lobby.
GAME_PROCESS: str = "League of Legends.exe"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Compiled regex: a JWT/base64url token run — 100+ contiguous chars from the alphabet
# [A-Za-z0-9_\-\.].  A logged-in RiotGamesPrivateSettings.yaml contains an RSO refresh
# token / JWT whose base64url-encoded segments are far longer than 100 chars.  A fresh
# (not-yet-logged-in) config file contains only short YAML keys/values and will NOT match.
_TOKEN_RE: re.Pattern = re.compile(r"[A-Za-z0-9_\-.]{100,}")


def _looks_logged_in(text: str) -> bool:
    """Return True if *text* contains a JWT/base64url opaque run of >= 100 chars.

    A freshly-initialised or logged-out RiotGamesPrivateSettings.yaml holds only
    short YAML keys and values — no run of 100+ consecutive base64url characters.
    After the user logs in, Riot writes the RSO refresh token (a long opaque string)
    into the file, triggering this predicate.

    Args:
        text: The full text content of SESSION_FILE.

    Returns:
        True  if at least one 100+-character base64url run is present.
        False otherwise.
    """
    return bool(_TOKEN_RE.search(text))


def _snapshot_path(username: str) -> pathlib.Path:
    """Return the per-user snapshot file path (under %APPDATA%\\LoLSwitcher\\sessions\\)."""
    import config as _cfg  # local import to avoid circular deps at module level
    return _cfg.snapshot_dir(username) / "RiotGamesPrivateSettings.yaml"


def _file_looks_logged_in(path: pathlib.Path) -> bool:
    """Return True iff *path* exists, is >= 100 bytes, and looks logged-in (D-20/D-21).

    Single shared predicate for the "does this session file look logged in"
    heuristic — exists -> size >= 100 bytes -> ``_looks_logged_in(content)``.
    ``refresh_snapshot()`` and ``save_snapshot_now()`` (the refresh-outgoing
    write guard, D-20) and ``is_snapshot_stale()`` (the D-21 UI hint) all
    delegate to this ONE predicate so the thresholds can never silently drift
    apart between call sites (RESEARCH.md "Don't Hand-Roll").

    Security (D-22/T-04-01): callers must treat the return value as the only
    thing that crosses any trust boundary — this helper itself never logs or
    returns the file's content, size, or path.

    Args:
        path: Path to the session file to inspect (either the live
              ``SESSION_FILE`` or a per-account snapshot file).

    Returns:
        True  if path exists, is >= 100 bytes, and contains a 100+ char
              base64url token run (``_looks_logged_in``).
        False otherwise (including on any OSError while reading).
    """
    if not path.exists():
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size < 100:
        return False
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return _looks_logged_in(content)


def snapshot_exists(username: str) -> bool:
    """Return True iff a session snapshot file exists for ``username``.

    Pre-flight guard used by core.perform_switch BEFORE stop(): if no snapshot
    exists the switch cannot complete, so the running Riot/League session must
    NOT be terminated. Mirrors the file that swap_session() restores, so the two
    stay in agreement (D-34 / no destructive failure)."""
    return _snapshot_path(username).exists()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_game_running() -> bool:
    """Return True iff a real League of Legends match process is running (D-07/D-08).

    Checks only for ``League of Legends.exe`` (case-insensitive) — NOT the Riot
    Client or League Client lobby processes.  A match guard MUST call this before
    any kill/swap to avoid interrupting an active game (SWITCH-02, D-07).

    Returns:
        True  if the game process is found in the OS process list.
        False otherwise (client/lobby running alone is fine — switch allowed).
    """
    target = GAME_PROCESS.lower()
    for proc in psutil.process_iter(["name"]):
        try:
            name = proc.info["name"]
            if name and name.lower() == target:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError, TypeError):
            pass
    return False


def is_client_running() -> bool:
    """Return True iff the Riot Client process is present (D-12/STATUS-01).

    Checks only for ``RiotClientServices.exe`` (case-insensitive) — the root
    parent process in RIOT_KILL_ORDER.  Mirrors is_game_running()'s exact
    psutil.process_iter idiom and exception guard tuple; scope is locked to
    this single process name (D-12) — this says nothing about auth state,
    only that the client process itself is present (login screen, lobby, or
    champ select all read identically; Pitfall 4).

    Returns:
        True  if RiotClientServices.exe is found in the OS process list.
        False otherwise (including on any psutil access error).
    """
    target = "RiotClientServices.exe".lower()
    for proc in psutil.process_iter(["name"]):
        try:
            name = proc.info["name"]
            if name and name.lower() == target:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError, TypeError):
            pass
    return False


def stop(timeout: float = 10.0) -> bool:
    """Kill all Riot/League processes in RIOT_KILL_ORDER and poll until all dead.

    Step 1 — Kill: Iterates the current process list and sends ``kill()`` to every
    process whose name (case-insensitive) appears in RIOT_KILL_ORDER.  ``kill()``
    is asynchronous on Windows; the process entry lingers until it finishes cleanup.

    Step 2 — Poll: Loops with a short interval checking whether any of the target
    names are still visible via ``psutil.process_iter``.  Returns ``True`` as soon
    as the list is empty, or ``False`` if the deadline passes first.

    There is NO fixed ``time.sleep()`` before returning — the function returns the
    moment the OS confirms all target processes are gone (CRIT-1 / COMMON-1).
    ``swap_session()`` MUST only be called after this returns ``True``.

    Args:
        timeout: Maximum seconds to wait for all processes to die.  Default 10 s.

    Returns:
        True  if all RIOT_KILL_ORDER processes are gone before the timeout.
        False if at least one process is still alive when the deadline passes.
    """
    names_lower = {n.lower() for n in RIOT_KILL_ORDER}

    # Step 1: Send kill signals to all matching live processes
    for proc in psutil.process_iter(["name", "pid"]):
        try:
            name = proc.info["name"]
            if name and name.lower() in names_lower:
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError, TypeError):
            pass

    # Step 2: Poll until all target processes have exited (or timeout)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        alive = []
        for p in psutil.process_iter(["name"]):
            try:
                name = p.info["name"]
                if name and name.lower() in names_lower:
                    alive.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError, TypeError):
                pass
        if not alive:
            return True
        time.sleep(0.2)  # polling interval — NOT a fixed gate for the swap

    return False  # timeout — caller must NOT proceed with swap


def find_riot_client_exe() -> Optional[pathlib.Path]:
    """Locate RiotClientServices.exe via the Windows Registry with hardcoded fallbacks.

    Checks HKLM and HKCU for the Riot Games uninstall registry key, then falls back
    to the two most common installation paths.

    Returns:
        A pathlib.Path to ``RiotClientServices.exe`` if found, or ``None``.
    """
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for key_path in (
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Riot Game riot_client",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Riot Game riot_client",
        ):
            try:
                key = winreg.OpenKey(hive, key_path)
                loc, _ = winreg.QueryValueEx(key, "InstallLocation")
                candidate = pathlib.Path(loc) / "RiotClientServices.exe"
                if candidate.exists():
                    return candidate
            except (FileNotFoundError, OSError):
                pass

    # Hardcoded fallback paths (most common install locations)
    for c in (
        r"C:\Riot Games\Riot Client\RiotClientServices.exe",
        r"C:\Program Files\Riot Games\Riot Client\RiotClientServices.exe",
    ):
        p = pathlib.Path(c)
        if p.exists():
            return p

    return None


def swap_session(username: str) -> None:
    """Atomically restore a per-account session snapshot to the live SESSION_FILE.

    The operation is:
      1. Resolve the per-account snapshot path.
      2. If the snapshot does not exist → raise FileNotFoundError (drives the
         first-login flow; D-02/D-03 — this is NOT an error, it is a signal).
      3. Copy the snapshot to a staging file in SESSION_FILE.parent.
      4. ``os.replace()`` the staging file onto SESSION_FILE — atomic on the same
         partition; guarantees no partially-written state (T-01-12 / CRIT-1).

    A small retry loop (3×, short backoff) handles transient PermissionError from
    vgtray.exe holding a file handle after the Riot processes are killed (Pitfall 6).
    The retry is only around the ``os.replace`` call — NOT a pre-swap fixed sleep.

    IMPORTANT: This function MUST only be called after ``stop()`` returns True.

    DECISION (Open Question 1): The Cookies\\ and Sessions\\ sibling subfolders are
    NOT cleared.  The snapshot-restore approach swaps ``RiotGamesPrivateSettings.yaml``
    verbatim (D-01); that file carries the RSO token required for auto-login.  Clearing
    sibling subfolders is revisited only if the Task 3 checkpoint shows auto-login
    failing with this default (documented follow-up, not changed here).

    Args:
        username: The Riot username whose snapshot to restore.

    Raises:
        FileNotFoundError: If no snapshot exists for ``username`` — first-login signal.
        PermissionError:   If the OS still holds a lock after all retries (rare).
    """
    snapshot = _snapshot_path(username)
    if not snapshot.exists():
        raise FileNotFoundError(
            f"Kein Snapshot fuer '{username}' vorhanden. "
            "Bitte einmal manuell einloggen, damit der Snapshot erstellt werden kann."
        )

    # Write to a staging file, then atomic replace
    staging = SESSION_FILE.parent / "_lolswitcher_staging.yaml"
    shutil.copy2(snapshot, staging)

    last_err: Optional[Exception] = None
    try:
        for attempt in range(3):
            try:
                os.replace(staging, SESSION_FILE)
                return  # success — os.replace consumed the staging file
            except PermissionError as exc:
                last_err = exc
                if attempt < 2:
                    time.sleep(0.3 * (attempt + 1))  # 0.3s, 0.6s backoff

        # All retries exhausted — re-raise the last PermissionError
        raise last_err  # type: ignore[misc]
    finally:
        # Remove the staging file if it survived (i.e. os.replace never consumed
        # it because every attempt failed).  This token-bearing file must NEVER
        # be left behind at a predictable path in the live Riot Data directory.
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass


def start(riot_exe: pathlib.Path) -> None:
    """Start the Riot Client non-blocking with the league_of_legends launch flags.

    Uses ``subprocess.Popen`` without ``shell=True`` to avoid shell injection
    and to keep the process management clean (T-01-13 / Security Domain).

    Args:
        riot_exe: Path to ``RiotClientServices.exe``.
    """
    subprocess.Popen(  # noqa: S603 — no shell, known executable
        [
            str(riot_exe),
            "--launch-product=league_of_legends",
            "--launch-patchline=live",
        ],
        # Do not inherit the parent's console; let the client run independently.
        # CREATE_NO_WINDOW ensures no console flashes during launch (D-13).
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def clear_session() -> None:
    """Remove the live SESSION_FILE so the next Riot Client start shows a fresh login screen.

    Deleting the local session file does NOT revoke the token server-side (unlike
    "Sign out" in the Riot Client UI). This allows a clean re-login for first-time
    account setup without invalidating existing tokens.

    Safe to call when SESSION_FILE does not exist (no-op).

    The same transient-PermissionError retry pattern as swap_session() is used:
    up to 3 attempts with short backoff in case vgtray.exe briefly holds the handle.

    IMPORTANT: This function MUST only be called after ``stop()`` returns True.

    Raises:
        PermissionError: If the OS still holds a lock after all retries (rare).
    """
    if not SESSION_FILE.exists():
        return  # no-op — success

    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            os.remove(SESSION_FILE)
            return  # success
        except FileNotFoundError:
            return  # already gone — success
        except PermissionError as exc:
            last_err = exc
            if attempt < 2:
                time.sleep(0.3 * (attempt + 1))  # 0.3s, 0.6s backoff

    # All retries exhausted — re-raise the last PermissionError
    raise last_err  # type: ignore[misc]


def refresh_snapshot(username: str) -> bool:
    """Copy the live SESSION_FILE into the per-account snapshot if it looks logged in.

    Keeps the outgoing account's snapshot current against single-use/rotated refresh
    tokens.  Should be called just before ``stop()`` so the most recent live token
    is preserved in the snapshot.

    Conditions for a successful refresh:
      1. SESSION_FILE must exist.
      2. Session file size must be >= 100 bytes.
      3. ``_looks_logged_in(content)`` must be True.

    If all conditions pass, SESSION_FILE is copied (shutil.copy2) into the per-user
    snapshot directory, overwriting the existing snapshot.  Returns True.

    If any condition fails, the existing snapshot is left untouched and False is
    returned — the caller should proceed without aborting the switch.

    Args:
        username: Riot username whose snapshot to refresh.

    Returns:
        True  if the live file was logged-in and the snapshot was refreshed.
        False if the live file is absent, too small, or contains no RSO token.
    """
    if not _file_looks_logged_in(SESSION_FILE):
        return False

    dest = _snapshot_path(username)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SESSION_FILE, dest)
    return True


def save_snapshot_now(username: str) -> bool:
    """Synchronously validate the live SESSION_FILE and save it as a snapshot.

    Called from the controller on the main thread when the user clicks
    "Login fertig — Snapshot speichern" (manual confirm flow, D-04).

    Validation:
      1. SESSION_FILE must exist.
      2. Session file size must be >= 100 bytes.
      3. ``_looks_logged_in(content)`` must be True — a 100+ char JWT/base64url
         token run must be present.  This catches "clicked too early / not yet
         logged in" mistakes and ensures the snapshot actually contains an RSO token.

    If all conditions pass, the live SESSION_FILE is copied atomically to the
    per-username snapshot directory and ``True`` is returned.

    If any condition fails (file absent, too small, or no token yet), no copy is
    made and ``False`` is returned.  The caller is responsible for showing the user
    a "noch kein Login erkannt" message and keeping the pending state active so the
    user can click again after completing the login.

    Args:
        username: Riot username — determines the snapshot destination path.

    Returns:
        True  if the live file looked logged-in and the snapshot was saved.
        False if the live file is absent, too small, or contains no RSO token.
    """
    # exists -> size >= 100 bytes -> token-like content (shared predicate, D-20/D-21)
    if not _file_looks_logged_in(SESSION_FILE):
        return False

    # All conditions met — copy to per-user snapshot directory
    dest = _snapshot_path(username)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SESSION_FILE, dest)
    return True


def is_snapshot_stale(username: str) -> bool:
    """Return True iff *username* has a stored snapshot that looks logged-out (D-21).

    Proactive "session possibly expired" detector: reuses the exact same
    ``_file_looks_logged_in`` predicate that gates the refresh-outgoing write
    guard (D-20) in ``refresh_snapshot``/``save_snapshot_now`` — one shared
    heuristic, not a third independent copy (RESEARCH.md "Don't Hand-Roll").

    Blind spot (documented, not fixed here — Pitfall 2): this only detects a
    LOCALLY empty/tokenless/small snapshot file. A well-formed, full-size RSO
    token that Riot has silently invalidated server-side still passes this
    check and returns False here — ``recapture_session`` (D-19) is the
    correct remedy for that undetectable case.

    Security (D-22/T-04-01): returns a bool ONLY — never logs or returns the
    snapshot's content, size, or path.

    Args:
        username: Riot username whose snapshot to check.

    Returns:
        True  if a snapshot file exists for ``username`` but fails the
              logged-in heuristic (tokenless/small).
        False if no snapshot exists, or the snapshot looks logged-in.
    """
    path = _snapshot_path(username)
    if not path.exists():
        return False
    return not _file_looks_logged_in(path)
