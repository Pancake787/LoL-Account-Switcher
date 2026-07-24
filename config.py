from __future__ import annotations

import json
import os
import pathlib

from models import Account, AppState

# ---------------------------------------------------------------------------
# Path constants — all derived from environment variables, never hardcoded
# ---------------------------------------------------------------------------
APP_DIR: pathlib.Path = pathlib.Path(os.environ["APPDATA"]) / "LoLSwitcher"
ACCOUNTS_JSON: pathlib.Path = APP_DIR / "accounts.json"
SESSIONS_DIR: pathlib.Path = APP_DIR / "sessions"


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


def load_state() -> AppState:
    """Load AppState from accounts.json.

    Returns an empty AppState when the file is missing (first run) or
    malformed/partial.  Never raises — the caller can always rely on a valid
    AppState being returned.

    Security: The JSON file must NEVER contain a "password" key.  This
    function ignores any such key even if it were somehow present.
    """
    if not ACCOUNTS_JSON.exists():
        return AppState()

    try:
        raw = ACCOUNTS_JSON.read_text(encoding="utf-8")
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
            region = entry.get("region", "EUW")
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

        return AppState(accounts=accounts, active_username=active_username)

    except Exception:
        # Malformed / partial file — return safe default
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
    }

    # Atomic write: serialise to a temp file in the same directory, then
    # os.replace onto the target.  A crash/power-loss mid-write can only ever
    # damage the temp file, never the live accounts.json — so a partial write
    # can never silently wipe every configured account (WR-01).
    tmp = ACCOUNTS_JSON.with_name(ACCOUNTS_JSON.name + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, ACCOUNTS_JSON)
