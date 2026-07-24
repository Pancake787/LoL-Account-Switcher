"""rank_service.py — Riot API integration for rank data retrieval.

Module boundary (ARCHITECTURE.md): imports ONLY requests, stdlib, and models.
MUST NOT import gui/, config, controller, credential_store, or riot_client.

Implements:
- RiotAPIError: typed exception carrying .status_code
- _handle_response: HTTP error table (200/401/403/404/429/5xx)
- resolve_puuid: Riot-ID → PUUID via account-v1 (regional host europe)
- fetch_entries: PUUID → league entries via league-v4/entries/by-puuid (2-call primary)
- fetch_summoner_id: PUUID → encryptedSummonerId via summoner-v4 (3-call fallback step 2a)
- fetch_entries_by_summoner: summonerId → league entries via league-v4/by-summoner (3-call fallback step 2b)
- parse_entries: list[dict] → RankInfo (D-21 both queues, D-22 format, D-28 stale field)
"""
from __future__ import annotations

import time
from typing import Optional
from urllib.parse import quote

import requests
import requests.exceptions

from models import QueueRank, RankInfo

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Regional routing host for account-v1 (europe covers EUW + EUNE).
REGIONAL_HOST: str = "europe.api.riotgames.com"

#: Platform hosts per region for league-v4 and summoner-v4 endpoints.
PLATFORM_HOSTS: dict[str, str] = {
    "EUW": "euw1.api.riotgames.com",
    "EUNE": "eun1.api.riotgames.com",
}

#: APEX tiers have no meaningful division string in the display (API returns "I" but
#: it is meaningless — all Master+ players share the same tier).
APEX_TIERS: frozenset[str] = frozenset({"MASTER", "GRANDMASTER", "CHALLENGER"})


# ---------------------------------------------------------------------------
# Typed exception
# ---------------------------------------------------------------------------


class RiotAPIError(Exception):
    """Raised when the Riot API returns a non-200 HTTP response.

    SECURITY (T-02-05): The api_key is NEVER included in the exception message
    or any log output. _handle_response does not receive the key; callers pass
    it only to requests.get headers.
    """

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(message)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _handle_response(resp: requests.Response) -> None:
    """Raise a typed RiotAPIError for any non-200 HTTP status code.

    Error table (Pattern 3 from RESEARCH.md):
    - 200: success → return None
    - 429: rate limited → read Retry-After header; raise RiotAPIError(429)
    - 401/403: auth failure → raise RiotAPIError(status_code)
    - 404: not found → raise RiotAPIError(404)
    - other: raise RiotAPIError(status_code) with generic message

    T-02-05: api_key is NEVER included in any raised message.
    T-02-06: 429 reads Retry-After instead of retrying in a tight loop.
    """
    if resp.status_code == 200:
        return
    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After", "60")
        raise RiotAPIError(429, f"Rate limit — retry nach {retry_after}s")
    if resp.status_code in (401, 403):
        raise RiotAPIError(resp.status_code, "API-Key ungültig oder ohne Berechtigung")
    if resp.status_code == 404:
        raise RiotAPIError(404, "Spieler nicht gefunden")
    raise RiotAPIError(resp.status_code, f"API-Fehler {resp.status_code}")


def _platform_host(region: str) -> str:
    """Resolve region string to platform host. Defaults to EUW for unknown regions."""
    return PLATFORM_HOSTS.get(region.upper(), PLATFORM_HOSTS["EUW"])


# ---------------------------------------------------------------------------
# 2-call chain (primary)
# ---------------------------------------------------------------------------


def resolve_puuid(game_name: str, tag_line: str, api_key: str) -> str:
    """Step 1: Riot-ID → PUUID via account-v1 by-riot-id (regional host 'europe').

    Args:
        game_name: The gameName portion of the Riot-ID (e.g. "Main").
        tag_line:  The tagLine portion (e.g. "EUW").
        api_key:   Riot personal API key — sent in X-Riot-Token header only.

    Returns:
        The PUUID string from the account-v1 response.

    Raises:
        RiotAPIError: on any non-200 HTTP response (404 if Riot-ID not found).

    T-02-05: api_key is NOT embedded in any raised exception message.
    """
    url = (
        f"https://{REGIONAL_HOST}/riot/account/v1/accounts/by-riot-id"
        f"/{quote(game_name, safe='')}/{quote(tag_line, safe='')}"
    )
    resp = requests.get(url, headers={"X-Riot-Token": api_key}, timeout=10)
    _handle_response(resp)
    return resp.json()["puuid"]


def fetch_entries(puuid: str, region: str, api_key: str) -> list[dict]:
    """Step 2 (primary): PUUID → league entries via league-v4/entries/by-puuid.

    Returns an empty list for unranked players (the API returns [] for no ranked games).

    Args:
        puuid:   Account PUUID from resolve_puuid.
        region:  "EUW" or "EUNE" — routes to the correct platform host.
        api_key: Riot personal API key.

    Raises:
        RiotAPIError: on any non-200 HTTP response.
            404 indicates the by-puuid endpoint may not be live on this region
            (Assumption A1 in RESEARCH.md) — caller should fall back to 3-call chain.
    """
    host = _platform_host(region)
    url = f"https://{host}/lol/league/v4/entries/by-puuid/{quote(puuid, safe='')}"
    resp = requests.get(url, headers={"X-Riot-Token": api_key}, timeout=10)
    _handle_response(resp)
    return resp.json()


# ---------------------------------------------------------------------------
# 3-call fallback chain (when by-puuid returns 404)
# ---------------------------------------------------------------------------


def fetch_summoner_id(puuid: str, region: str, api_key: str) -> str:
    """Fallback step 2a: PUUID → encryptedSummonerId via summoner-v4/by-puuid.

    WARNING: summoner-v4 had a bug in Aug 2025 (GitHub Issue #1092) where the
    'id' field was missing from the response. This function raises explicitly
    if the 'id' field is absent rather than silently returning None.

    Args:
        puuid:   Account PUUID.
        region:  "EUW" or "EUNE".
        api_key: Riot personal API key.

    Returns:
        The encryptedSummonerId string.

    Raises:
        RiotAPIError: on any non-200 HTTP response.
        ValueError: if the 'id' field is absent from the response (Aug-2025 bug).
    """
    host = _platform_host(region)
    url = f"https://{host}/lol/summoner/v4/summoners/by-puuid/{quote(puuid, safe='')}"
    resp = requests.get(url, headers={"X-Riot-Token": api_key}, timeout=10)
    _handle_response(resp)
    data = resp.json()
    summoner_id = data.get("id")
    if not summoner_id:
        # Explicit guard for the Aug-2025 missing-id bug (RESEARCH.md Pitfall 1).
        raise ValueError(
            f"summoner-v4 returned no 'id' field for puuid {puuid[:8]}… "
            "(see RESEARCH.md Pitfall 1 / GitHub Issue #1092)"
        )
    return summoner_id


def fetch_entries_by_summoner(summoner_id: str, region: str, api_key: str) -> list[dict]:
    """Fallback step 2b: encryptedSummonerId → league entries via league-v4/by-summoner.

    Args:
        summoner_id: Encrypted summoner ID from fetch_summoner_id.
        region:      "EUW" or "EUNE".
        api_key:     Riot personal API key.

    Returns:
        List of league entry dicts (empty list = unranked).

    Raises:
        RiotAPIError: on any non-200 HTTP response.
    """
    host = _platform_host(region)
    url = f"https://{host}/lol/league/v4/entries/by-summoner/{quote(summoner_id, safe='')}"
    resp = requests.get(url, headers={"X-Riot-Token": api_key}, timeout=10)
    _handle_response(resp)
    return resp.json()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_entries(entries: list[dict]) -> RankInfo:
    """Parse league-v4 entries list into a RankInfo dataclass.

    Maps:
    - "RANKED_SOLO_5x5" → RankInfo.solo (QueueRank)
    - "RANKED_FLEX_SR"  → RankInfo.flex (QueueRank)
    - Other queue types are ignored.

    APEX tiers (MASTER/GRANDMASTER/CHALLENGER): division is set to "" because
    the API always sends rank="I" for apex players, but the division is
    meaningless — all apex players share the tier. The display omits it.

    Args:
        entries: List of league entry dicts from fetch_entries or
                 fetch_entries_by_summoner. Empty list = unranked.

    Returns:
        RankInfo with solo/flex populated where available, fetched_at=time.time(),
        stale=False (fresh data from a successful API call).
    """
    rank = RankInfo(fetched_at=time.time(), stale=False)
    for entry in entries:
        queue = entry.get("queueType", "")
        if queue not in ("RANKED_SOLO_5x5", "RANKED_FLEX_SR"):
            continue

        tier = entry.get("tier", "").upper()
        division = "" if tier in APEX_TIERS else entry.get("rank", "")
        q = QueueRank(
            tier=tier,
            division=division,
            lp=entry.get("leaguePoints", 0),
            wins=entry.get("wins", 0),
            losses=entry.get("losses", 0),
        )
        if queue == "RANKED_SOLO_5x5":
            rank.solo = q
        else:
            rank.flex = q

    return rank
