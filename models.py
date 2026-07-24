from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SwitchStatus(Enum):
    IDLE = "idle"
    SWITCHING = "switching"
    ERROR = "error"


@dataclass
class Account:
    username: str          # Riot-Benutzername (unveraenderlich)
    display_name: str      # Anzeigename (z.B. "Main", "Smurf")
    has_snapshot: bool = False  # True wenn RiotGamesPrivateSettings.yaml gespeichert
    # Phase 2 additions — all Optional with defaults so existing JSON migrates cleanly
    riot_id: Optional[str] = None       # "gameName#tagLine" — nur fuer API-Lookup
    region: str = "EUW"                 # "EUW" oder "EUNE"
    puuid: Optional[str] = None         # cached after first resolve_puuid call
    rank_cache: Optional[dict] = None   # serialised RankInfo; None = never loaded
    rank_cache_ts: Optional[float] = None  # time.time() of last successful fetch


# ---------------------------------------------------------------------------
# Phase 2: Rank data model
# ---------------------------------------------------------------------------

#: APEX tiers have no meaningful division (always "I" in the API, but omitted in display).
_APEX_TIERS = {"MASTER", "GRANDMASTER", "CHALLENGER"}


@dataclass
class QueueRank:
    """Rank information for a single ranked queue."""
    tier: str       # "GOLD", "MASTER" — uppercase from API
    division: str   # "II", "I" — "" for APEX tiers (MASTER/GRANDMASTER/CHALLENGER)
    lp: int
    wins: int
    losses: int

    @property
    def display(self) -> str:
        """Human-readable rank string.

        Normal: "Gold II — 47 LP (124S/98N)"
        APEX:   "Master — 1000 LP (80S/60N)"  (no division)
        """
        tier_cap = self.tier.capitalize()
        if self.tier in _APEX_TIERS:
            return f"{tier_cap} — {self.lp} LP ({self.wins}S/{self.losses}N)"
        return f"{tier_cap} {self.division} — {self.lp} LP ({self.wins}S/{self.losses}N)"


@dataclass
class RankInfo:
    """Parsed ranked-queue data for an account."""
    solo: Optional[QueueRank] = None    # RANKED_SOLO_5x5; None = unranked
    flex: Optional[QueueRank] = None    # RANKED_FLEX_SR; None = unranked
    fetched_at: Optional[float] = None  # time.time() — for stale detection
    stale: bool = False                 # True if last fetch failed but cache exists


@dataclass
class AppState:
    accounts: list[Account] = field(default_factory=list)
    active_username: Optional[str] = None   # Username des aktiven Accounts
    status: SwitchStatus = SwitchStatus.IDLE
    status_message: str = ""
