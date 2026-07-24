from __future__ import annotations

import json
import os
import pathlib
import shutil
import tempfile

from models import Account, AppState

# ---------------------------------------------------------------------------
# Path constants — all derived from environment variables, never hardcoded
# ---------------------------------------------------------------------------
APP_DIR: pathlib.Path = pathlib.Path(os.environ["APPDATA"]) / "LoLSwitcher"
ACCOUNTS_JSON: pathlib.Path = APP_DIR / "accounts.json"
SESSIONS_DIR: pathlib.Path = APP_DIR / "sessions"

# ---------------------------------------------------------------------------
# Region migration (REGION-02, D-12) — Phase 8
# ---------------------------------------------------------------------------

#: Legacy pre-Phase-8 region strings (bare "EUW"/"EUNE", no numeral suffix) mapped
#: to their canonical Riot platform ids. "EUNE" -> "EUN1" is the load-bearing,
#: easy-to-miss case (Pitfall 2) — a generic .upper() pass would NOT catch it.
_LEGACY_REGION_ALIASES: dict[str, str] = {"EUW": "EUW1", "EUNE": "EUN1"}


def _normalize_region(raw: str) -> str:
    """Normalize a region string to a canonical Riot platform id.

    Legacy values ("EUW", "EUNE") are silently migrated to canonical platform
    ids ("EUW1", "EUN1") on load — D-12: no dialog, no user-visible step, only
    a test-covered in-memory upgrade. Any other value is upper-cased as-is;
    whitelist enforcement against the canonical id set happens at the
    controller entry points (Plan 08-03), not here.
    """
    return _LEGACY_REGION_ALIASES.get(raw.upper(), raw.upper())


def snapshot_dir(username: str) -> pathlib.Path:
    """Return the snapshot directory for a given Riot username.

    The directory is ``SESSIONS_DIR / username``.  It is NOT created here —
    creation happens lazily when the snapshot is first saved (D-04), and the
    directory is removed by ``Controller.delete_account`` during cleanup (D-13).

    Args:
        username: The Riot username identifying the account.

    Returns:
        A pathlib.Path pointing to the per-user snapshot directory.
    """
    return SESSIONS_DIR / username


def ensure_dirs() -> None:
    """Create application directories if they do not exist yet."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _backup_path() -> pathlib.Path:
    """Rolling backup of the last known-good accounts.json (derived at call time
    so tests that override ACCOUNTS_JSON get the matching backup path)."""
    return ACCOUNTS_JSON.with_name(ACCOUNTS_JSON.name + ".bak")


def _corrupt_path() -> pathlib.Path:
    """Where a corrupt accounts.json is quarantined for forensics/manual recovery."""
    return ACCOUNTS_JSON.with_name(ACCOUNTS_JSON.name + ".corrupt")


def _state_from_json(raw: str) -> AppState:
    """Parse accounts.json content into an AppState. Raises on malformed JSON.

    Security: The JSON file must NEVER contain a "password" key.  This function
    ignores any such key even if it were somehow present.
    """
    data = json.loads(raw)

    accounts: list[Account] = []
    for entry in data.get("accounts", []):
        username = entry.get("username")
        display_name = entry.get("display_name")
        if not username or not display_name:
            # Skip malformed entries rather than crashing
            continue
        has_snapshot = bool(entry.get("has_snapshot", False))
        # Phase 2 additions — defensive .get() with defaults so Phase-1
        # accounts.json (which lacks these keys) loads without error.
        riot_id = entry.get("riot_id") or None
        # REGION-02/D-12: legacy "EUW"/"EUNE" silently migrate to "EUW1"/"EUN1".
        region = _normalize_region(entry.get("region", "EUW1"))
        puuid = entry.get("puuid") or None
        rank_cache = entry.get("rank_cache") or None
        rank_cache_ts = entry.get("rank_cache_ts") or None
        accounts.append(Account(
            username=username,
            display_name=display_name,
            has_snapshot=has_snapshot,
            riot_id=riot_id,
            region=region,
            puuid=puuid,
            rank_cache=rank_cache,
            rank_cache_ts=rank_cache_ts,
        ))

    active_username = data.get("active_username") or None

    # Phase 8 additions — app-wide settings fields, read at the top level (not
    # per-account), mirroring the active_username default-handling above.
    # Defensive .get() with defaults so pre-Phase-8 accounts.json (lacking
    # these keys entirely) loads without error.
    language = data.get("language") or None
    update_check_enabled = bool(data.get("update_check_enabled", True))
    dismissed_update_version = data.get("dismissed_update_version") or None
    disable_gpu = bool(data.get("disable_gpu", True))
    update_last_checked = float(data.get("update_last_checked", 0.0) or 0.0)

    return AppState(
        accounts=accounts,
        active_username=active_username,
        language=language,
        update_check_enabled=update_check_enabled,
        dismissed_update_version=dismissed_update_version,
        disable_gpu=disable_gpu,
        update_last_checked=update_last_checked,
    )


def _try_load(path: pathlib.Path) -> AppState | None:
    """Return an AppState parsed from *path*, or None if it is missing,
    unreadable, or malformed. Never raises."""
    if not path.exists():
        return None
    try:
        return _state_from_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_state() -> AppState:
    """Load AppState from accounts.json, with automatic backup recovery.

    Never raises — the caller can always rely on a valid AppState being
    returned.  Returns an empty AppState on first run (no file).

    Data-safety hardening (WR-01, after observed concurrent-write corruption):
    if the primary file is corrupt, the corrupt copy is quarantined as
    ``accounts.json.corrupt`` and the last known-good ``accounts.json.bak`` is
    restored and returned when available — so a single bad write can no longer
    silently wipe the configured account list.
    """
    primary = _try_load(ACCOUNTS_JSON)
    if primary is not None:
        return primary

    # Primary missing or corrupt.
    if ACCOUNTS_JSON.exists():
        # Quarantine the corrupt file so it is not overwritten by the next save
        # and remains available for manual recovery.
        try:
            os.replace(ACCOUNTS_JSON, _corrupt_path())
        except OSError:
            pass

        # Attempt recovery from the rolling backup.
        recovered = _try_load(_backup_path())
        if recovered is not None:
            # Restore the backup as the live file so the next save builds on a
            # known-good base rather than starting empty.
            try:
                shutil.copy2(_backup_path(), ACCOUNTS_JSON)
            except OSError:
                pass
            return recovered

    return AppState()


def save_state(state: AppState) -> None:
    """Persist AppState to accounts.json.

    Security: Only username, display_name, has_snapshot, and the Phase-2
    non-secret public fields (riot_id, region, puuid, rank_cache, rank_cache_ts)
    are written.  Passwords and the API key are NEVER stored in this file
    (T-01-04, T-02-09).
    """
    ensure_dirs()

    data = {
        "accounts": [
            {
                "username": account.username,
                "display_name": account.display_name,
                "has_snapshot": account.has_snapshot,
                # Phase 2 additions — all non-secret public data (T-02-10)
                "riot_id": account.riot_id,
                "region": account.region,
                "puuid": account.puuid,
                "rank_cache": account.rank_cache,       # serialised dict or None
                "rank_cache_ts": account.rank_cache_ts,  # float or None
            }
            for account in state.accounts
        ],
        "active_username": state.active_username,
        # Phase 8 additions — app-wide settings, flat primitives (T-02-09: never
        # write secrets; the API key lives only in keyring/DPAPI, never here).
        "language": state.language,
        "update_check_enabled": state.update_check_enabled,
        "dismissed_update_version": state.dismissed_update_version,
        "disable_gpu": state.disable_gpu,
        "update_last_checked": state.update_last_checked,
    }

    payload = json.dumps(data, ensure_ascii=False, indent=2)

    # Roll the current known-good file into a backup BEFORE overwriting, so a
    # later corrupt/partial write can be recovered on load. Only back up when
    # the current file still parses — never overwrite a good backup with a bad
    # file (that would destroy the only recovery source).
    if ACCOUNTS_JSON.exists() and _try_load(ACCOUNTS_JSON) is not None:
        try:
            shutil.copy2(ACCOUNTS_JSON, _backup_path())
        except OSError:
            pass  # best-effort; a failed backup must not block the save

    # Atomic write with a UNIQUE per-write temp file (via mkstemp) in the same
    # directory, then os.replace onto the target. A crash/power-loss mid-write
    # can only ever damage the temp file, never the live accounts.json (WR-01).
    # The unique name is load-bearing: a fixed temp name let two concurrent
    # app instances clobber the same temp file and leave trailing garbage —
    # the observed corruption. mkstemp guarantees a distinct file per write.
    fd, tmp_name = tempfile.mkstemp(
        dir=str(ACCOUNTS_JSON.parent), prefix="accounts.", suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, ACCOUNTS_JSON)
    except BaseException:
        # Never leave a stray temp file behind on failure.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
