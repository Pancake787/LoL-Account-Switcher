"""update_checker.py — throttled GitHub Releases update check (ONBOARD-03).

Module boundary (mirrors rank_service.py / status_poller.py): imports ONLY
requests + stdlib (time, json — json is unused directly, kept implicit via
requests.Response.json()) plus the standalone, dependency-free ``version``
module (WR-01 — single source of truth for APP_VERSION). MUST NOT import
gui/controller/credential_store/config. This module MUST NOT be imported
from the CLI/headless switch path (main.py switch branch / core.py) — the
Phase-7 CI import gate protects the webview boundary; this module is only
ever imported from the GUI startup path (wired in Plan 08-06).

Design (RESEARCH.md Pattern 4 / Pitfall 4):
- The real throttle is an app-level TTL (CHECK_TTL_S), NOT HTTP ETag/304
  caching — the 304-is-free exemption only applies to authenticated GitHub
  API clients, and this app never ships an Authorization header/token in a
  public client (T-08-05).
- Silent on every failure path: TTL not elapsed, network error, 403 rate
  limit, any non-200 status. Never raises, never surfaces an error to the
  user (D-13/D-14 UX requirement) — the caller simply gets None and tries
  again on the next TTL window / app launch.
- GET /repos/{owner}/{repo}/releases/latest already excludes prereleases and
  drafts server-side, so no -rc/-beta suffix parsing is needed here.
"""
from __future__ import annotations

import time

import requests
import requests.exceptions

from version import APP_VERSION

#: Canonical current app version, re-exported here for backward compatibility
#: with existing call sites (``update_checker.APP_VERSION``). The single
#: source of truth is ``version.APP_VERSION`` (WR-01) — kept in lockstep with
#: installer/LoLSwitcher.iss's ``AppVersion=`` and enforced by
#: tests/test_version_sync.py. Do not hardcode a duplicate value here.
__all__ = ["APP_VERSION", "REPO", "CHECK_TTL_S", "check_for_update"]

#: Hardcoded owner/repo — never derived from user input (no SSRF surface, T-08-04).
REPO: str = "Pancake787/LoL-Account-Switcher"

#: 24h app-level throttle. GitHub's unauthenticated rate limit is 60 req/h/IP;
#: this floor keeps the app to at most one check per day regardless of how
#: often the caller invokes check_for_update (T-08-06).
CHECK_TTL_S: int = 24 * 60 * 60


def _parse_semver(tag: str) -> tuple[int, ...]:
    """Parse a ``vX.Y.Z``-style tag into a comparable int tuple.

    Args:
        tag: A version tag such as ``"v2.2.0"`` or ``"2.2.0"``.

    Returns:
        A tuple of ints, e.g. ``(2, 2, 0)``.
    """
    return tuple(int(p) for p in tag.lstrip("v").split("."))


def check_for_update(current_version: str, last_checked_at: float) -> dict | None:
    """Check GitHub Releases for a newer version than *current_version*.

    Args:
        current_version: The app's own version string (e.g. ``APP_VERSION``).
        last_checked_at: Unix timestamp of the last check attempt (0.0 if
            never checked before) — used for the CHECK_TTL_S throttle.

    Returns:
        ``{"tag_name": str, "html_url": str}`` when a strictly-newer release
        exists, else ``None``. Never raises — every failure path (TTL not
        elapsed, network error, rate limit, non-200) silently returns None.
    """
    if time.time() - last_checked_at < CHECK_TTL_S:
        return None  # throttled — skip the network call entirely (Pitfall 4)

    try:
        resp = requests.get(
            f"https://api.github.com/repos/{REPO}/releases/latest", timeout=10
        )
    except requests.exceptions.RequestException:
        return None  # silent on network failure — never surface as an error

    if resp.status_code == 403:
        return None  # rate-limited — try again next TTL window
    if resp.status_code != 200:
        return None

    data = resp.json()
    latest_tag = data.get("tag_name", "")
    if not latest_tag:
        return None

    try:
        is_newer = _parse_semver(latest_tag) > _parse_semver(current_version)
    except ValueError:
        # Malformed tag (non-numeric segment) — never crash on an untrusted
        # response body; treat as "no update" rather than raising.
        return None

    if is_newer:
        return {"tag_name": latest_tag, "html_url": data.get("html_url")}
    return None
