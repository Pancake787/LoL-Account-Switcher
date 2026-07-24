from __future__ import annotations

import os
import re
import shutil
import threading
import time
from typing import Optional

import requests.exceptions

import config
import core
import credential_store
import rank_service
import riot_client
from gui import _clipboard, i18n
from models import Account, AppState, RankInfo, SwitchStatus

#: Plan 08-05 (ONBOARD-04): pulls the retry-after seconds out of rank_service's
#: hardcoded "Rate limit — retry nach {n}s" RiotAPIError message so it can be
#: re-interpolated into the i18n "riotapi.rate_limit" key in the current
#: language (rank_service.py stays GUI-free/untranslated per its module
#: boundary — Plan 08-05 does not modify it).
_RETRY_SECONDS_RE = re.compile(r"(\d+)")


def _extract_retry_seconds(message: str) -> str:
    """Best-effort extraction of the retry-after seconds from a RiotAPIError message.

    Falls back to "60" (matches rank_service._handle_response's own default
    Retry-After) if no digits are found — never raises.
    """
    match = _RETRY_SECONDS_RE.search(message)
    return match.group(1) if match else "60"


def _translate_riot_error(exc: rank_service.RiotAPIError) -> str:
    """Translate a rank_service.RiotAPIError into current-language catalog text.

    Plan 08-05 (ONBOARD-04, Pitfall 3): rank_service.py raises with hardcoded
    German text (its module boundary keeps it GUI-free/i18n-free — Plan 08-05
    does not modify it). This maps the status code to the matching
    ``riotapi.*`` strings.json key so the message crosses the bridge
    translated, without rank_service ever needing to know about i18n.
    """
    if exc.status_code in (401, 403):
        return i18n.t("riotapi.key_unauthorized")
    if exc.status_code == 404:
        return i18n.t("riotapi.player_not_found")
    if exc.status_code == 429:
        return i18n.t("riotapi.rate_limit", n=_extract_retry_seconds(str(exc)))
    return i18n.t("riotapi.api_error", code=exc.status_code)

# ---------------------------------------------------------------------------
# Module-level constants for rank refresh orchestration (D-23 / D-24)
# ---------------------------------------------------------------------------
#: Interval between repeating rank refreshes (15 minutes in milliseconds).
RANK_REFRESH_INTERVAL_MS: int = 15 * 60 * 1000

#: Cache TTL for deciding staleness (30 minutes in seconds — informational).
RANK_CACHE_TTL_S: int = 30 * 60


class Controller:
    """Owns the AppState and orchestrates all business logic.

    The Controller is the single owner of AppState.  The pywebview window is
    stored as self._window; state changes are published to the JS layer via
    _push_state() which writes window.state.* properties.  No business logic
    lives in the GUI bridge (js_api.py).
    """

    #: How long a copied password stays on the clipboard before auto-clear (D-06 / CR-03).
    CLIPBOARD_CLEAR_MS: int = 30_000

    #: Interval for polling accounts.json mtime to detect external changes (D-35 / SD-06).
    _POLL_INTERVAL_MS: int = 2000

    def __init__(self, root) -> None:
        """Initialise the controller.

        Args:
            root: The pywebview Window object (or any object with a .state
                  attribute for testing).  Kept as `root` for call-site
                  compatibility; stored internally as self._window.
        """
        self._window = root
        self._last_switch_target: Optional[Account] = None  # for retry_switch()
        self._pending_first_login: Optional[Account] = None  # D-04 manual confirm flow
        #: STATUS-01 (D-17): ephemeral live-status state, NOT part of AppState/
        #: accounts.json. Set via update_client_status(), called from
        #: StatusPoller.on_change (daemon thread, wired in gui/webview_window.py).
        self._client_running: bool = False
        self._game_live: bool = False
        #: Plan 08-04 (D-09): persistent header hint set on a 401/403 rank-fetch
        #: error, cleared on the next successful rank fetch. Ephemeral, like
        #: _client_running/_game_live — never persisted to accounts.json.
        self._api_key_warning: bool = False
        #: Plan 08-05 (ONBOARD-04, D-15/D-16): the current status key + params
        #: mirrored to window.state.status_key/status_params (via _push_state)
        #: so app.js can re-resolve them from strings.json in any language.
        #: Ephemeral, like _client_running/_api_key_warning — never persisted.
        self._status_key: Optional[str] = None
        self._status_params: dict = {}
        #: Set by webview_window.start() after construction; stopped centrally
        #: in shutdown() (WR-03). None when no poller was wired (e.g. headless/tests).
        self._status_poller = None
        #: WR-03: thread-safe shutdown flag.  Written by shutdown() (GUI thread)
        #: and read by every daemon-timer callback.  A threading.Event is the
        #: idiomatic cross-thread flag (atomic set/is_set) — the ``_shutting_down``
        #: property below preserves the historical bool attribute API.
        self._shutdown_event: threading.Event = threading.Event()
        #: WR-02: live daemon timers, cancelled together in shutdown() so no
        #: callback can run against a torn-down window and the clipboard-clear
        #: timer never leaks.  Guarded by _timers_lock for cross-thread mutation.
        self._timers: set = set()
        self._timers_lock: threading.Lock = threading.Lock()

        config.ensure_dirs()
        self.state: AppState = config.load_state()

        # Plan 08-05 (D-15): first-run default is the System-Locale; a
        # persisted user choice (self.state.language already set from a prior
        # run/config.save_state) always wins over re-detection. Setting
        # gui.i18n's current language here (not just on the first Settings
        # open) means every Python-originated status/error message resolves
        # in the right language from the very first message (D-16).
        if self.state.language is None:
            self.state.language = i18n.detect_default_language()
        i18n.set_language(self.state.language)

        # D-35 / SD-06: track accounts.json mtime to detect external changes
        self._accounts_json_mtime: float = self._get_accounts_mtime()
        #: Guard against reentrant poll scheduling (WR-06b).
        self._poll_in_progress: bool = False
        self._schedule_accounts_poll()

    @property
    def _shutting_down(self) -> bool:
        """WR-03: thread-safe view of the shutdown flag (backed by an Event).

        Kept as a property so existing call sites (and tests that assign
        ``ctrl._shutting_down = True``) continue to work unchanged while the
        underlying state is a ``threading.Event``.
        """
        return self._shutdown_event.is_set()

    @_shutting_down.setter
    def _shutting_down(self, value: bool) -> None:
        if value:
            self._shutdown_event.set()
        else:
            self._shutdown_event.clear()

    def _start_timer(self, delay_s: float, fn) -> Optional[threading.Timer]:
        """Create, track, and start a daemon timer (WR-02).

        The timer is registered in ``self._timers`` so ``shutdown()`` can cancel
        every outstanding timer.  The wrapper discards the timer from the set
        when its callback completes so the set does not grow unbounded.

        Returns None (and starts nothing) when already shutting down, so a
        late reschedule cannot resurrect the timer loop after teardown.
        """
        if self._shutting_down:
            return None
        timer_ref: dict = {}

        def _wrapped() -> None:
            try:
                fn()
            finally:
                t = timer_ref.get("t")
                if t is not None:
                    with self._timers_lock:
                        self._timers.discard(t)

        t = threading.Timer(delay_s, _wrapped)
        t.daemon = True
        timer_ref["t"] = t
        with self._timers_lock:
            self._timers.add(t)
        t.start()
        return t

    def shutdown(self) -> None:
        """Mark the controller as shutting down and cancel all live timers (WR-02/WR-03).

        Called from the window's close protocol.  Sets the shutdown Event so the
        repeating rank-refresh timer, the accounts poll, and the game-end poll
        loop stop rescheduling themselves, then cancels every outstanding
        ``threading.Timer`` (including a pending clipboard-clear timer) so no
        callback fires against a destroyed window.

        Note (D-06): a pending clipboard-clear timer is cancelled rather than
        run eagerly.  Clearing eagerly would require retaining the copied
        password on the controller (violating the "password never cached"
        invariant) or clobbering unrelated clipboard content the user copied
        afterwards.  The OS keeps whatever is on the clipboard; the 30 s
        auto-clear is a best-effort guarantee only while the app is running.
        """
        self._shutdown_event.set()
        with self._timers_lock:
            timers = list(self._timers)
            self._timers.clear()
        for t in timers:
            t.cancel()
        # STATUS-01/WR-03: centralized poller teardown — single stop() call
        # site, not a second one in js_api.close(). Safe when no poller was
        # ever wired (e.g. headless/tests).
        if getattr(self, "_status_poller", None) is not None:
            self._status_poller.stop()

    # ------------------------------------------------------------------
    # State publication (replaces _notify / add_listener listener pattern)
    # ------------------------------------------------------------------

    def _push_state(self) -> None:
        """Publish current AppState to the JS layer via window.state.

        Thread-safe: window.state writes are pywebview's documented cross-thread
        channel.  Call after any AppState mutation that the UI should reflect.

        No-op when _shutting_down is True (WR-03).
        """
        if self._shutting_down:
            return
        window = self._window
        window.state.accounts = self._serialize_accounts()
        window.state.active_username = self.state.active_username
        window.state.status = self.state.status.value          # "idle"/"switching"/"error"
        window.state.status_message = self.state.status_message
        window.state.pending_first_login = (
            self._pending_first_login.username
            if self._pending_first_login else None
        )
        # STATUS-01 (D-17): live client/game status — top-level writes only.
        window.state.client_running = self._client_running
        window.state.game_live = self._game_live
        # Plan 08-04 (ONBOARD-01/02): API-key presence + persistent expiry hint.
        # has_api_key drives the "Kein API-Key" rank-tile hint (D-01);
        # api_key_warning drives the persistent header hint on 401/403 (D-09).
        window.state.has_api_key = self.has_api_key()
        window.state.api_key_warning = self._api_key_warning
        # Plan 08-05 (ONBOARD-04): key+params status contract + current
        # language, so app.js can resolve/re-resolve status text via the
        # shared strings.json and live-re-render on a language switch (D-16).
        window.state.status_key = self._status_key
        window.state.status_params = self._status_params
        window.state.language = self.state.language

    def update_client_status(self, client_running: bool, game_live: bool) -> None:
        """Handle a status transition from StatusPoller.on_change (STATUS-01/D-17).

        Called from the poller's daemon thread — window.state is the
        documented thread-safe Python->JS channel, so no main-thread
        dispatch is required (mirrors _post_status/_post_error).

        Args:
            client_running: True iff RiotClientServices.exe is present.
            game_live:       True iff League of Legends.exe is present.
        """
        self._client_running = client_running
        self._game_live = game_live
        self._push_state()

    def _serialize_accounts(self) -> list:
        """Serialize accounts for the JS layer (Tier/Division only — D-14).

        Returns a list of dicts.  Each dict contains only public, non-secret
        fields (T-04-01): no password, no API key, no RSO token.  rank_cache
        is included as-is (already a JSON-safe dict from _rank_info_to_dict).

        ``session_warning`` (D-21/D-22, SESSION-01) is a bool ONLY, derived
        from ``riot_client.is_snapshot_stale`` — never the snapshot's content,
        size, or path.  Always False when ``has_snapshot`` is False (nothing
        to be stale about yet).

        Returns:
            List of account dicts with keys: username, display_name,
            has_snapshot, riot_id, region, rank_cache, session_warning.
        """
        result = []
        for acc in self.state.accounts:
            result.append({
                "username": acc.username,
                "display_name": acc.display_name,
                "has_snapshot": acc.has_snapshot,
                "riot_id": acc.riot_id,
                "region": acc.region,
                "rank_cache": acc.rank_cache or {},
                "session_warning": (
                    riot_client.is_snapshot_stale(acc.username)
                    if acc.has_snapshot else False
                ),
            })
        return result

    # ------------------------------------------------------------------
    # accounts.json mtime-Watcher (D-35 / SD-06)
    # ------------------------------------------------------------------

    def _get_accounts_mtime(self) -> float:
        """Return the mtime of accounts.json as float, or 0.0 if the file is missing.

        Used by the mtime-poll loop to detect external changes to accounts.json
        without importing any additional dependencies (os only, D-35).

        Returns:
            mtime as float (seconds since epoch); 0.0 when OSError (file absent).
        """
        try:
            return os.path.getmtime(config.ACCOUNTS_JSON)
        except OSError:
            return 0.0

    def _schedule_accounts_poll(self) -> None:
        """Schedule the next accounts.json mtime poll (D-35 / SD-06).

        Uses a daemon threading.Timer.  Does nothing when the controller is
        shutting down (WR-03).
        """
        self._start_timer(self._POLL_INTERVAL_MS / 1000, self._poll_accounts_json)

    def _poll_accounts_json(self) -> None:
        """Poll accounts.json mtime and reload active_username on external changes.

        Called on the main thread by the repeating timer set up in
        ``_schedule_accounts_poll``.  Detects changes made by the CLI
        (``lolswitcher switch <username>``) or any other external writer and
        propagates the new ``active_username`` to the GUI without triggering a
        write-loop (D-35 / SD-06 / Pitfall 4).

        Loop-Schutz (Pitfall 4): after any own ``config.save_state`` call in
        the Controller, ``_accounts_json_mtime`` is immediately updated so the
        next poll does NOT treat the own write as an external change.

        WR-03: guarded against teardown — if the window is closing, the poll
        stops and does not reschedule.  ``TclError`` is swallowed to end the
        loop cleanly (mirrors ``_schedule_rank_refresh_timer`` pattern).
        """
        if self._shutting_down:
            return
        # Guard against reentrant execution when root.after() is synchronous
        # (e.g. test fakes that execute callbacks immediately regardless of delay).
        # Without this guard, _schedule_accounts_poll -> after -> _poll_accounts_json
        # -> _schedule_accounts_poll -> ... causes infinite recursion (WR-06b).
        if self._poll_in_progress:
            return
        self._poll_in_progress = True
        try:
            current_mtime = self._get_accounts_mtime()
            if current_mtime != self._accounts_json_mtime:
                self._accounts_json_mtime = current_mtime
                new_state = config.load_state()
                self.state.active_username = new_state.active_username
                self._push_state()
        except Exception:  # noqa: BLE001 — WR-07: a single transient read error
            # (e.g. a JSON parse error while an external writer is mid-write, or a
            # momentary file lock) must NOT freeze all live updates.  Previously
            # this set _shutting_down=True, which also killed the rank-refresh
            # timer, game-end poll, and every _push_state for the rest of the
            # session.  Swallow the error and let the poll reschedule below so the
            # watcher recovers on the next tick.
            pass
        finally:
            self._poll_in_progress = False
            # Always reschedule (unless shutting down — _start_timer guards on it)
            # so one bad read never disables the watcher permanently (WR-07).
            self._schedule_accounts_poll()

    # ------------------------------------------------------------------
    # Account management
    # ------------------------------------------------------------------

    def add_account(
        self,
        display_name: str,
        username: str,
        password: str,
        riot_id: Optional[str] = None,
        region: str = "EUW",
    ) -> None:
        """Add a new account and persist its credential in Windows Credential Manager.

        Validates all three required fields and rejects duplicates.  The password is
        passed straight to ``credential_store.save`` and is NEVER written into
        ``accounts.json`` or cached on ``AppState`` / ``Account`` (T-01-04, SEC-1).

        Phase 2: Accepts optional ``riot_id`` (e.g. "Main#EUW") and ``region``.
        If ``riot_id`` is provided AND an API key is present, the PUUID is resolved
        immediately via account-v1; a 404 raises ValueError and the account is NOT
        added (D-19 / T-02-08).  If no API key is present, validation is deferred
        and the account is stored with ``puuid=None`` (D-19 deferred path).

        Args:
            display_name: Human-readable name shown in the UI (e.g. "Main").
            username:     Riot username — unique identifier, immutable after creation.
            password:     Account password — stored ONLY in Windows Credential Manager.
            riot_id:      Optional Riot-ID "gameName#tagLine" (e.g. "Main#EUW").
            region:       Any canonical Riot platform id (e.g. "EUW1", "NA1", "KR")
                          or the legacy bare "EUW"/"EUNE" strings (auto-normalized).
                          Default "EUW" (normalized to "EUW1").

        Raises:
            ValueError: If any required field is empty/whitespace, ``username``
                        already exists, ``riot_id`` contains path separators (T-02-08),
                        ``region`` is not a recognized platform id (T-08-08 / Pitfall 5),
                        or the API returns 404 for the Riot-ID.
        """
        if not display_name or not display_name.strip():
            raise ValueError(i18n.t("error.name_empty"))
        if not username or not username.strip():
            raise ValueError(i18n.t("error.username_empty"))
        if not password or not password.strip():
            raise ValueError(i18n.t("error.password_empty"))

        # Normalise once: the stripped username is the single identity used as the
        # WCM key, the snapshot directory name, AND the duplicate-detection key —
        # mirroring how display_name is stored stripped (WR-07).  Backward
        # compatible: existing un-stripped entries in accounts.json are untouched.
        username = username.strip()

        # CR-01 / T-03-01: reject path separators in the username before it is
        # stored — username is used as the snapshot directory name (SESSIONS_DIR
        # / username) and the WCM key, so a traversing value would let later
        # os.replace / shutil.rmtree calls escape the app dir.  Mirrors the
        # riot_id separator check below; raises ValueError the dialog surfaces.
        core.validate_username(username)

        for acc in self.state.accounts:
            if acc.username == username:
                raise ValueError(i18n.t("error.account_exists", username=username))

        # Phase 2: validate riot_id format before storing credentials (T-02-08)
        puuid: Optional[str] = None
        if riot_id:
            riot_id = riot_id.strip()
            # Reject path separators to prevent URL-path injection (T-02-08 / ASVS V5)
            if "/" in riot_id or "\\" in riot_id:
                raise ValueError(i18n.t("error.riot_id_slash"))

        # T-08-08 / Pitfall 5 (ASVS V5): whitelist the region BEFORE any Riot API
        # host is built. Case-insensitive; legacy bare "EUW"/"EUNE" strings are
        # normalized to their canonical platform id (mirrors rank_service.
        # platform_host's defensive alias normalization) rather than silently
        # defaulting to EUW. Anything else outside PLATFORM_TO_REGIONAL is rejected.
        region_upper = region.strip().upper()
        region = rank_service._LEGACY_PLATFORM_ALIASES.get(region_upper, region_upper)
        if region not in rank_service.PLATFORM_TO_REGIONAL:
            raise ValueError(i18n.t("error.region_invalid"))

        # Phase 2: resolve PUUID if riot_id provided and API key is present (D-19).
        # WR-04: PUUID resolution runs BEFORE the credential is stored — resolution
        # does not need the stored credential, so a resolution failure (404 /
        # network error / malformed Riot-ID) can never leave an orphaned WCM entry.
        if riot_id:
            api_key = credential_store.get_api_key()
            if api_key:
                # Split "gameName#tagLine" — reject if malformed
                parts = riot_id.split("#", 1)
                if len(parts) != 2:
                    raise ValueError(i18n.t("error.riot_id_format"))
                # WR-05: strip both parts and reject whitespace-only segments so a
                # malformed Riot-ID (e.g. "Name# ") never reaches the API.
                game_name, tag_line = parts[0].strip(), parts[1].strip()
                if not game_name or not tag_line:
                    raise ValueError(i18n.t("error.riot_id_format"))
                try:
                    puuid = rank_service.resolve_puuid(
                        game_name, tag_line, api_key, platform_id=region
                    )
                except rank_service.RiotAPIError as exc:
                    if exc.status_code == 404:
                        raise ValueError(i18n.t("error.riot_id_not_found")) from exc
                    raise ValueError(_translate_riot_error(exc)) from exc
                except requests.exceptions.RequestException as exc:
                    # CR-02: network/transport failure (timeout, connection error).
                    # Convert to a ValueError the dialog can surface inline.
                    raise ValueError(i18n.t("error.riot_id_network")) from exc
            # else: deferred validation — store with puuid=None (D-19)

        # Store password in Windows Credential Manager — NOT in accounts.json.
        # Deferred until AFTER validation/resolution succeeds (WR-04).
        credential_store.save(username, password)

        # Append metadata (no password field) and persist.  WR-04: if persistence
        # fails (e.g. an OSError from save_state), roll back BOTH the in-memory
        # mutations and the just-stored WCM credential so no orphaned secret is
        # left behind and a later re-add is not blocked by a phantom credential.
        new_account = Account(
            username=username,
            display_name=display_name.strip(),
            has_snapshot=False,
            riot_id=riot_id or None,
            region=region,
            puuid=puuid,
        )
        previous_active = self.state.active_username
        self.state.accounts.append(new_account)
        # First account becomes the active one (ACCT-04)
        if self.state.active_username is None:
            self.state.active_username = username

        try:
            config.save_state(self.state)
        except Exception:  # noqa: BLE001 — roll back on any persistence failure
            self.state.accounts.remove(new_account)
            self.state.active_username = previous_active
            credential_store.delete(username)
            raise

        self._push_state()

    def delete_account(self, username: str) -> None:
        """Remove an account and clean up all associated data (D-13).

        Removes:
        - The account metadata from ``state.accounts`` and ``accounts.json``
        - The credential from Windows Credential Manager
        - The session snapshot directory under ``SESSIONS_DIR / username``

        If the deleted account was active, ``active_username`` is updated to
        the first remaining account, or ``None`` if none remain.

        Args:
            username: The Riot username of the account to remove.
        """
        self.state.accounts = [
            acc for acc in self.state.accounts if acc.username != username
        ]

        # Remove credential from Windows Credential Manager
        credential_store.delete(username)

        # Remove session snapshot directory (D-13 full cleanup)
        shutil.rmtree(config.snapshot_dir(username), ignore_errors=True)

        # Update active_username if it pointed at the deleted account
        if self.state.active_username == username:
            remaining = self.state.accounts
            self.state.active_username = remaining[0].username if remaining else None

        config.save_state(self.state)
        self._push_state()

    def rename_account(self, username: str, new_display_name: str) -> None:
        """Rename the display name of an account without altering its Riot identity (D-14, ACCT-03).

        Only ``display_name`` is changed — ``username`` and ``has_snapshot`` remain
        untouched (T-01-09).  The change is persisted to ``accounts.json`` immediately.

        Args:
            username:         The Riot username of the account to rename.
            new_display_name: The new human-readable display name.

        Raises:
            ValueError: If ``new_display_name`` is empty or whitespace (German message).
        """
        if not new_display_name or not new_display_name.strip():
            raise ValueError(i18n.t("error.name_empty"))

        for acc in self.state.accounts:
            if acc.username == username:
                acc.display_name = new_display_name.strip()
                break

        config.save_state(self.state)
        self._push_state()

    def reorder_accounts(self, new_order: list[str]) -> None:
        """Reorder accounts to match the provided username list and persist the result (D-17).

        Rebuilds ``state.accounts`` in the order given by ``new_order``.  Unknown
        usernames in ``new_order`` are ignored (T-01-10).  Any existing account
        whose username is missing from ``new_order`` is appended at the end so no
        account is ever lost (T-01-10).  ``active_username`` is not changed.

        Args:
            new_order: List of Riot usernames in the desired order.
        """
        existing = {acc.username: acc for acc in self.state.accounts}

        reordered: list = []
        seen: set = set()
        for username in new_order:
            if username in existing and username not in seen:
                reordered.append(existing[username])
                seen.add(username)

        # Append any account not referenced in new_order (defensive, T-01-10)
        for acc in self.state.accounts:
            if acc.username not in seen:
                reordered.append(acc)

        self.state.accounts = reordered
        config.save_state(self.state)
        self._push_state()

    def copy_password(self, username: str) -> bool:
        """Fetch the stored password on demand and place it on the system clipboard.

        This is the D-06 affordance: the password is retrieved from Windows
        Credential Manager ONLY when the user explicitly clicks "Passwort kopieren".
        It is NEVER cached in ``AppState``, in any ``Account`` field, or in any
        other controller attribute.  It is NEVER auto-typed or injected via
        UI-automation APIs (per D-06 and REQUIREMENTS Out-of-Scope "Credential
        Auto-Typing").

        Args:
            username: The Riot username whose stored password to copy.

        Returns:
            ``True``  if the password was found and copied to the clipboard.
            ``False`` if no password is stored for this username.
        """
        # Fetch on demand — do NOT log or store the password value
        pw = credential_store.get(username)
        if not pw:
            return False

        _clipboard.clipboard_set(pw)

        # Auto-clear the clipboard after CLIPBOARD_CLEAR_MS so the password does
        # not linger in the global buffer indefinitely.  The clear only fires if
        # the clipboard still holds the copied value (T-04-02 / match-before-clear).
        self._start_timer(
            self.CLIPBOARD_CLEAR_MS / 1000,
            lambda value=pw: self._clear_clipboard_if_matches(value),
        )
        # pw goes out of scope here — not retained on AppState/Account
        return True

    def _clear_clipboard_if_matches(self, value: str) -> None:
        """Clear the system clipboard only if it still holds *value* (T-04-02).

        Uses ctypes clipboard helpers (no tkinter).  Best-effort: any failure
        is swallowed so the timer callback can never crash the app.
        """
        try:
            if _clipboard.clipboard_get() == value:
                _clipboard.clipboard_clear()
        except Exception:  # noqa: BLE001 — clipboard access is best-effort
            pass

    def set_riot_id(
        self,
        username: str,
        riot_id: Optional[str],
        region: str = "EUW",
    ) -> None:
        """Update the Riot-ID and region on an existing account (edit path, D-18/D-19/D-20).

        Changes ONLY ``riot_id``, ``region``, and ``puuid`` — never ``username``
        or ``has_snapshot`` (same contract as ``rename_account`` for display_name).

        If an API key is present and ``riot_id`` is provided, resolves the PUUID
        immediately.  If no key is present, defers validation (``puuid=None``).

        Args:
            username: Riot username of the existing account to edit.
            riot_id:  New Riot-ID "gameName#tagLine" or None to clear.
            region:   Any canonical Riot platform id (e.g. "EUW1", "NA1", "KR")
                      or the legacy bare "EUW"/"EUNE" strings (auto-normalized).

        Raises:
            ValueError: If ``riot_id`` contains path separators (T-02-08),
                        ``region`` is not a recognized platform id (T-08-08 /
                        Pitfall 5), or the API returns 404 for the Riot-ID.
        """
        if riot_id:
            riot_id = riot_id.strip()
            if "/" in riot_id or "\\" in riot_id:
                raise ValueError(i18n.t("error.riot_id_slash"))

        # T-08-08 / Pitfall 5 (ASVS V5): whitelist the region BEFORE any Riot API
        # host is built — see add_account for the identical rationale/shape.
        region_upper = region.strip().upper()
        region = rank_service._LEGACY_PLATFORM_ALIASES.get(region_upper, region_upper)
        if region not in rank_service.PLATFORM_TO_REGIONAL:
            raise ValueError(i18n.t("error.region_invalid"))

        puuid: Optional[str] = None
        if riot_id:
            api_key = credential_store.get_api_key()
            if api_key:
                parts = riot_id.split("#", 1)
                if len(parts) != 2:
                    raise ValueError(i18n.t("error.riot_id_format"))
                # WR-05: strip both parts and reject whitespace-only segments.
                game_name, tag_line = parts[0].strip(), parts[1].strip()
                if not game_name or not tag_line:
                    raise ValueError(i18n.t("error.riot_id_format"))
                try:
                    puuid = rank_service.resolve_puuid(
                        game_name, tag_line, api_key, platform_id=region
                    )
                except rank_service.RiotAPIError as exc:
                    if exc.status_code == 404:
                        raise ValueError(i18n.t("error.riot_id_not_found")) from exc
                    raise ValueError(_translate_riot_error(exc)) from exc
                except requests.exceptions.RequestException as exc:
                    # CR-02: convert network/transport failure to a ValueError the
                    # RiotIdDialog can surface inline.
                    raise ValueError(i18n.t("error.riot_id_network")) from exc

        for acc in self.state.accounts:
            if acc.username == username:
                acc.riot_id = riot_id or None
                acc.region = region
                acc.puuid = puuid
                break

        config.save_state(self.state)
        self._push_state()
        # Trigger a rank refresh so the edited Riot-ID loads immediately without
        # a restart — mirrors save_api_key's D-23 Trigger 1 pattern (gap fix D-23).
        self._trigger_rank_refresh()

    # ------------------------------------------------------------------
    # Switch orchestration (SWITCH-01 / SWITCH-02 / SWITCH-03)
    # ------------------------------------------------------------------

    def switch_account(self, target: Account) -> None:
        """Initiate a one-click account switch.

        **Match guard (D-07 / SWITCH-02):** If ``League of Legends.exe`` is
        running, sets ERROR status with a German block message and returns
        immediately WITHOUT killing any process or starting a background thread.
        There is NO override button — this is a hard block.

        **Normal path:** Sets SWITCHING status and launches ``_do_switch`` on a
        daemon background thread.  All subsequent status updates are posted back
        to the main thread via ``root.after(0, ...)``.

        Args:
            target: The ``Account`` to switch to.
        """
        self._last_switch_target = target

        if riot_client.is_game_running():
            # Hard block — no kill, no swap, no thread (D-07, SWITCH-02)
            self._set_status(SwitchStatus.ERROR, "status.blocked_match")
            return

        # Snapshot the shared state the worker needs, on the MAIN THREAD, before
        # spawning the daemon.  The worker must NOT read self.state.accounts /
        # self.state.active_username directly: the main thread can still mutate
        # those (add/delete/rename) while the switch is in flight, and iterating
        # a list from another thread can raise or read stale data (WR-02).
        active_username, snapshot_usernames = self._snapshot_switch_state()

        # Start the switch sequence on a background thread
        self._set_status(SwitchStatus.SWITCHING, "status.killing_client")
        threading.Thread(
            target=self._do_switch,
            args=(target, active_username, snapshot_usernames),
            daemon=True,
        ).start()

    def _snapshot_switch_state(self) -> tuple[Optional[str], set]:
        """Return an immutable snapshot of the state the switch worker reads.

        Called on the main thread.  Returns ``(active_username, set_of_usernames_
        with_snapshots)`` so the background thread never touches the mutable
        ``self.state.accounts`` list (WR-02).
        """
        return (
            self.state.active_username,
            {a.username for a in self.state.accounts if a.has_snapshot},
        )

    def _do_switch(
        self,
        target: Account,
        active_username: Optional[str] = None,
        snapshot_usernames: Optional[set] = None,
    ) -> None:
        """Execute the kill → swap → start sequence on a background thread.

        **Routing logic (D-30):**
        - Accounts WITH a snapshot: delegate to ``core.perform_switch`` (shared
          with the CLI headless path).  This removes the duplicated kill→swap→start
          sequence from the GUI path.
        - Accounts WITHOUT a snapshot: First-Login flow (clear_session +
          _enter_pending_first_login) — this GUI-only path is preserved here
          because it requires the manual "Login fertig" confirm button.  core.py
          cannot drive first-login (D-34 / no GUI).

        Worker thread — mutates AppState and calls _push_state().  No tkinter
        main-thread requirement.  Session file state is left intact on any
        failure (safe state, D-12).

        Args:
            target: The ``Account`` to switch to.
            active_username:    Snapshot of ``state.active_username`` captured on
                                the main thread (WR-02).  Falls back to reading it
                                live when called directly (e.g. tests).
            snapshot_usernames: Snapshot of the set of usernames that have a
                                snapshot, captured on the main thread (WR-02).
        """
        # Fall back to a fresh main-thread-equivalent snapshot when invoked
        # without pre-captured values (keeps direct calls / tests working).
        if active_username is None and snapshot_usernames is None:
            active_username, snapshot_usernames = self._snapshot_switch_state()
        elif snapshot_usernames is None:
            snapshot_usernames = {
                a.username for a in self.state.accounts if a.has_snapshot
            }

        try:
            if target.has_snapshot:
                # ------------------------------------------------------------------
                # Snapshot-Pfad (D-30): delegiere an core.perform_switch
                # core.py fuehrt Step 0 (refresh outgoing) + Step 1 (stop) +
                # Step 2 (swap) + Step 3 (start) durch.
                # ------------------------------------------------------------------
                result = core.perform_switch(target.username)

                if result == core.SwitchResult.SUCCESS:
                    # Step 4: mark switch as done — direct call on worker thread;
                    # _on_switch_done only mutates AppState + _push_state (thread-safe).
                    self._on_switch_done(target)
                elif result == core.SwitchResult.BLOCKED:
                    self._post_error("status.blocked_match")
                elif result == core.SwitchResult.STOP_FAILED:
                    self._post_error("status.kill_failed")
                elif result == core.SwitchResult.NO_SNAPSHOT:
                    # Should not occur for has_snapshot=True, but handle defensively
                    self._post_error("status.no_snapshot")
                elif result == core.SwitchResult.RIOT_NOT_FOUND:
                    self._post_error("status.riot_not_found_switched")
                else:
                    # core.SwitchResult.ERROR or any future value
                    self._post_error("status.unknown_error")

            else:
                # ------------------------------------------------------------------
                # First-Login-Pfad (D-04): kein Snapshot vorhanden
                # Diese GUI-only Sequenz bleibt hier, da sie das manuelle
                # "Login fertig"-Confirm erfordert (core.py hat kein GUI, D-34).
                # ------------------------------------------------------------------

                # Step 0 (best-effort): refresh outgoing snapshot before killing
                if (
                    active_username is not None
                    and active_username in snapshot_usernames
                ):
                    try:
                        riot_client.refresh_snapshot(active_username)
                    except Exception:  # noqa: BLE001
                        pass  # best-effort

                # Step 1: kill Riot/League processes + poll until dead
                self._post_status("status.killing_client")
                if not riot_client.stop(timeout=10.0):
                    self._post_error("status.kill_failed")
                    return  # safe state — no swap attempted (D-12)

                # Refresh the outgoing account's snapshot first (if any) so it keeps its
                # latest token before we clear the live session file.
                if (
                    active_username is not None
                    and active_username in snapshot_usernames
                ):
                    try:
                        riot_client.refresh_snapshot(active_username)
                    except Exception:  # noqa: BLE001
                        pass  # best-effort

                # Clear the live session so Riot opens a fresh login screen (NOT "Sign out").
                # Deleting the local file does NOT revoke the token server-side.
                self._post_status("status.switching_session")
                riot_client.clear_session()

                riot_exe = riot_client.find_riot_client_exe()
                if riot_exe is None:
                    # Riot Client executable not found — do NOT enter the pending
                    # state telling the user to log into a client that never opened (WR-04).
                    self._post_error("status.riot_not_found_manual")
                    return  # safe state — no pending first-login
                riot_client.start(riot_exe)
                # Enter pending-first-login state — direct call on worker thread;
                # _enter_pending_first_login mutates AppState + _push_state (thread-safe).
                self._enter_pending_first_login(target)
                return  # pending state; user must click confirm button

        except Exception as exc:  # noqa: BLE001
            self._post_error("status.unknown_error_exc", exc=exc)

    def _enter_pending_first_login(self, target: Account) -> None:
        """Enter the pending-first-login state for *target* (D-04 manual confirm flow).

        Must be called on the main thread.  Sets ``_pending_first_login`` and posts
        the instruction status so the GUI can show the confirm button.

        Args:
            target: The ``Account`` awaiting its first-login snapshot.
        """
        self._pending_first_login = target
        self._set_status(
            SwitchStatus.SWITCHING,
            "status.pending_login",
            name=target.display_name,
        )

    def confirm_first_login_snapshot(self) -> None:
        """Validate the live SESSION_FILE and save a snapshot for the pending account.

        Called from the GUI on the main thread when the user clicks
        "Login fertig — Snapshot speichern".

        Success path:
          - ``riot_client.save_snapshot_now`` copies SESSION_FILE → snapshot dir.
          - ``has_snapshot`` is set to True and persisted.
          - ``active_username`` is updated (D-09).
          - Pending state is cleared; status set to IDLE.
          - Listeners are notified (GUI refreshes).

        Failure path (file not yet in logged-in state):
          - Nothing is copied; no state is mutated.
          - Status message tells the user to log in first and try again.
          - Pending state is kept so the user can retry.

        Does nothing if not currently in pending-first-login state.
        """
        target = self._pending_first_login
        if target is None:
            return

        success = riot_client.save_snapshot_now(target.username)

        if success:
            # Mark snapshot saved, update active account, clear pending state
            target.has_snapshot = True
            self.state.active_username = target.username
            config.save_state(self.state)
            self._accounts_json_mtime = self._get_accounts_mtime()  # D-35 Loop-Schutz
            self._pending_first_login = None
            self._set_status(SwitchStatus.IDLE, "status.snapshot_saved")
        else:
            # Not logged in yet — inform the user and keep pending state
            self._set_status(SwitchStatus.SWITCHING, "status.no_login_yet")

    def cancel_first_login(self) -> None:
        """Clear the pending-first-login state without capturing a snapshot.

        Called from the GUI "Abbrechen" affordance.  Leaves the account
        with ``has_snapshot = False`` (unchanged).  Status is reset to IDLE.
        """
        if self._pending_first_login is None:
            return
        self._pending_first_login = None
        self._set_status(SwitchStatus.IDLE, "")

    # ------------------------------------------------------------------
    # Session re-capture (SESSION-01 / D-19)
    # ------------------------------------------------------------------

    def recapture_session(self, username: str) -> None:
        """Re-run the first-login capture flow for an existing account (D-19).

        Rescue path for an account whose stored snapshot has gone stale/
        logged-out (server-side token invalidation cannot be detected by the
        refresh-outgoing guard — see ``riot_client.is_snapshot_stale``).
        Reuses the EXISTING first-login flow verbatim: ``clear_session()`` ->
        find/start the Riot Client -> ``_enter_pending_first_login`` (yellow
        pending state, "Login fertig"/"Abbrechen" buttons) ->
        ``confirm_first_login_snapshot`` saves the fresh snapshot. No new
        pending-state machine is introduced.

        Looks up the account on the calling thread (fast, no I/O); the
        silent no-op on an unknown username mirrors ``switch_account``'s
        guard against stale JS state. The actual work (``clear_session``/
        ``start`` do file I/O + process spawn) runs on a background daemon
        thread, mirroring ``switch_account``'s ``threading.Thread`` wrapping.

        Args:
            username: Riot username of the account to re-capture.
        """
        acc = next((a for a in self.state.accounts if a.username == username), None)
        if acc is None:
            return  # unknown username — guard against stale JS state
        # Snapshot the shared state the worker needs, on the CALLING thread, before
        # spawning the daemon (WR-02 parity with switch_account): the worker must not
        # iterate self.state.accounts from another thread while the main thread can
        # still mutate it (add/delete/rename).
        active_username, snapshot_usernames = self._snapshot_switch_state()
        threading.Thread(
            target=self._do_recapture,
            args=(acc, active_username, snapshot_usernames),
            daemon=True,
        ).start()

    def _do_recapture(
        self,
        acc: Account,
        active_username: Optional[str] = None,
        snapshot_usernames: Optional[set] = None,
    ) -> None:
        """Worker-thread body for ``recapture_session`` (D-19).

        Mirrors the first-login branch of ``_do_switch``: kill the running Riot
        Client, refresh the outgoing account's snapshot, clear the live session,
        restart the client to a fresh login screen, then enter the pending-first-
        login state.  The Riot Client is single-instance, so WITHOUT the kill step
        ``start()`` below is a silent no-op whenever a client is already running —
        that is the "second account does nothing (no close, no login window)" bug.

        Match guard (T-05-09 / SWITCH-02 parity): if League of Legends is
        currently running, this is a hard block — no kill, no ``clear_session()``,
        no Riot restart. There is no override, mirroring ``switch_account``'s
        guard against interrupting the user's own live match.

        Args:
            acc: The ``Account`` whose session should be re-captured.
            active_username:    Snapshot of ``state.active_username`` captured on
                                the calling thread (WR-02).  Falls back to a live
                                read when called directly (e.g. tests).
            snapshot_usernames: Snapshot of the set of usernames that have a
                                snapshot, captured on the calling thread (WR-02).
        """
        if riot_client.is_game_running():
            self._set_status(SwitchStatus.ERROR, "status.recapture_blocked")
            return

        # Fall back to a fresh snapshot when invoked without pre-captured values
        # (keeps direct calls / tests working).
        if active_username is None and snapshot_usernames is None:
            active_username, snapshot_usernames = self._snapshot_switch_state()
        elif snapshot_usernames is None:
            snapshot_usernames = {
                a.username for a in self.state.accounts if a.has_snapshot
            }

        # Step 1: kill Riot/League processes + poll until dead.  Required before
        # start() — a single-instance client that is already running would not be
        # restarted otherwise (the reported second-account no-op).
        self._post_status("status.killing_client")
        if not riot_client.stop(timeout=10.0):
            self._post_error("status.kill_failed")
            return  # safe state — no clear/restart attempted (D-12)

        # Refresh the outgoing (currently-active) account's snapshot so it keeps
        # its latest token before we clear the live session file (D-20 parity).
        # ``refresh_snapshot`` self-guards against overwriting a good snapshot with
        # a logged-out session (is_snapshot_stale / _file_looks_logged_in).
        if active_username is not None and active_username in snapshot_usernames:
            try:
                riot_client.refresh_snapshot(active_username)
            except Exception:  # noqa: BLE001
                pass  # best-effort

        # Clear the live session so Riot opens a fresh login screen (NOT "Sign out").
        self._post_status("status.recapture_resetting")
        riot_client.clear_session()

        riot_exe = riot_client.find_riot_client_exe()
        if riot_exe is None:
            self._post_error("status.recapture_not_found")
            return
        riot_client.start(riot_exe)

        # Enter pending-first-login state — direct call on worker thread;
        # _enter_pending_first_login mutates AppState + calls _push_state
        # (window.state is the documented thread-safe cross-thread channel).
        self._enter_pending_first_login(acc)

    def _on_switch_done(self, target: Account) -> None:
        """Handle a successful switch on the main thread.

        Sets ``state.active_username`` to ``target.username`` (D-09 — the marker
        is set internally by the app, NEVER read from Riot's undocumented files).
        An out-of-app login may leave this marker stale — accepted limitation (D-10).

        Phase 2: Triggers a rank refresh (D-23 Trigger 2) and starts game-end
        polling (D-23 Trigger 3) after a successful switch.

        Args:
            target: The ``Account`` that was successfully switched to.
        """
        # Internal active marker — only ever set by the app (D-09)
        self.state.active_username = target.username
        config.save_state(self.state)
        self._accounts_json_mtime = self._get_accounts_mtime()  # D-35 Loop-Schutz
        # _set_status already calls _notify() — do not notify a second time here
        # (a double rebuild destroys/recreates every card twice and flickers; WR-05).
        self._set_status(
            SwitchStatus.IDLE,
            "status.done_active",
            name=target.display_name,
        )
        # Phase 2: trigger rank refresh after switch (D-23 Trigger 2)
        self._trigger_rank_refresh()
        # Phase 2: start polling for game end to trigger refresh when League closes (D-23 Trigger 3)
        self._start_game_end_polling()

    def retry_switch(self) -> None:
        """Re-invoke switch_account with the last target account (D-12 retry path).

        Called from the GUI "Erneut versuchen" button shown after a switch error.
        Does nothing if no prior switch target is recorded.
        """
        if self._last_switch_target is not None:
            self.switch_account(self._last_switch_target)

    # ------------------------------------------------------------------
    # Phase 2 — API key management (D-25, D-26, RANK-02)
    # ------------------------------------------------------------------

    def save_api_key(self, key: str) -> None:
        """Validate the candidate key live, then store it and refresh ranks.

        Plan 08-04 (D-03/D-08, ONBOARD-01/02): BEFORE storing, calls
        ``rank_service.validate_api_key`` (a cheap status-v4 call requiring no
        Riot ID/PUUID). An invalid/expired key (401/403 -> False) or a network
        failure raises ``ValueError`` and the key is NEVER stored or logged
        (T-08-11/T-02-05) — this is the same "validate BEFORE persist" shape
        used by ``add_account``'s riot_id/PUUID resolution. Only on success:
        delegates to credential_store.save_api_key (DPAPI/WCM), clears the
        persistent expiry hint, and triggers an immediate rank refresh (D-08 —
        same trigger logic as after a Riot-ID edit).

        Args:
            key: The personal Riot API key entered by the user.

        Raises:
            ValueError: If the key is invalid/expired, a network error occurs,
                or the Riot API returns a transient error (429/5xx).
        """
        try:
            valid = rank_service.validate_api_key(key)
        except rank_service.RiotAPIError as exc:
            # 429/5xx — transient Riot-side error, not a verdict on the key itself.
            raise ValueError(_translate_riot_error(exc)) from exc
        except requests.exceptions.RequestException as exc:
            raise ValueError(i18n.t("error.api_key_network")) from exc

        if not valid:
            raise ValueError(i18n.t("error.api_key_invalid"))

        credential_store.save_api_key(key)
        self._api_key_warning = False  # D-09: a freshly validated key clears the hint
        self._set_status(SwitchStatus.IDLE, "status.api_key_saved")
        # Trigger immediate rank refresh now that a key is available (D-08 / D-23 Trigger 1)
        self._trigger_rank_refresh()

    def has_api_key(self) -> bool:
        """Return True if a Riot API key is currently stored (WCM or DPAPI file).

        Returns:
            bool: True if a non-empty key is stored; False otherwise.
        """
        return bool(credential_store.get_api_key())

    def get_api_key_masked(self) -> str:
        """Return a fixed 8-bullet mask if a key is stored, else an empty string.

        The mask intentionally does NOT reveal the real key length (T-02-02).

        Returns:
            str: '••••••••' if a key is stored, '' otherwise.
        """
        return "••••••••" if self.has_api_key() else ""

    def delete_api_key(self) -> None:
        """Delete the stored Riot API key (both WCM entry and DPAPI file).

        Plan 08-04 (ONBOARD-02): clears the persistent expiry hint (D-09) and
        pushes state so the Settings modal + rank tiles immediately reflect
        the "no key" state. The key value is never logged (T-08-11).
        """
        credential_store.delete_api_key()
        credential_store.delete_api_key_file()
        self._api_key_warning = False
        self._push_state()

    def get_settings(self) -> dict:
        """Return the current app-wide settings for the Settings modal (ONBOARD-02).

        Never includes the raw API key — only the fixed 8-bullet mask
        (T-08-11 / get_api_key_masked's own no-length-leak guarantee).

        Returns:
            dict: ``{"has_api_key": bool, "api_key_masked": str,
            "language": str|None, "update_check_enabled": bool,
            "disable_gpu": bool}``.
        """
        return {
            "has_api_key": self.has_api_key(),
            "api_key_masked": self.get_api_key_masked(),
            "language": self.state.language,
            "update_check_enabled": self.state.update_check_enabled,
            "disable_gpu": self.state.disable_gpu,
        }

    def set_gpu(self, enabled: bool) -> None:
        """Persist the GPU-acceleration toggle (D-07 — effective after restart).

        Args:
            enabled: True to enable GPU acceleration (disable_gpu=False).
        """
        self.state.disable_gpu = not enabled
        config.save_state(self.state)

    def set_language(self, lang: str) -> None:
        """Switch the active UI language live, persist it, and push state (ONBOARD-04).

        Plan 08-05 (D-15/D-16): sets ``gui.i18n``'s module-level current
        language so every subsequent Python-originated status/error message
        resolves in the new language starting with the very next message
        (D-16 — Python side has no history to re-translate). Persists the
        choice to ``accounts.json`` so it survives a restart (overriding the
        first-run System-Locale default from then on), and pushes state so
        ``app.js``'s ``applyLanguage()`` re-renders the whole UI immediately.

        Args:
            lang: Language code, e.g. "de" or "en". No validation is
                performed (mirrors ``gui.i18n.set_language``'s own
                never-raises contract) — an unrecognized code simply
                resolves every key to its raw-key fallback.
        """
        i18n.set_language(lang)
        self.state.language = lang
        config.save_state(self.state)
        self._push_state()

    # ------------------------------------------------------------------
    # Phase 2 — Rank fetch orchestration (D-23/D-24/D-27/D-28)
    # ------------------------------------------------------------------

    def refresh_ranks(self) -> None:
        """Manually refresh all account ranks (user-initiated — e.g. the ↻ button).

        Thin public wrapper over ``_trigger_rank_refresh`` so the GUI never calls
        a private method. No-op (placeholders stay) when no API key is stored.
        Background fetches post back to the main thread (D-20).
        """
        self._trigger_rank_refresh()

    def _trigger_rank_refresh(self) -> None:
        """Start background rank fetches for all accounts with a known PUUID.

        Reads the API key on-demand from keyring; returns immediately if no key
        is stored (D-27 — placeholders stay).  Spawns one daemon thread per
        eligible account.  Does NOT touch the switch spinner or status bar
        (UI-SPEC: no spinner for rank refreshes, silent except on 401/403).

        Thread safety: only reads acc.puuid / acc.region / acc.username from the
        main thread before spawning; the worker receives these as plain args.

        Pitfall 5 guard: iterates on ``acc.puuid`` (not ``acc.riot_id``) — an
        account with riot_id but puuid=None is skipped.
        """
        api_key = credential_store.get_api_key()
        if not api_key:
            return  # No key → all cards stay in placeholder state (D-27)

        for acc in self.state.accounts:
            if not acc.puuid:
                continue  # Pitfall 5: skip accounts without a resolved PUUID
            threading.Thread(
                target=self._fetch_rank_for_account,
                args=(acc.username, acc.puuid, acc.region, api_key),
                daemon=True,
            ).start()

    def _fetch_rank_for_account(
        self,
        username: str,
        puuid: str,
        region: str,
        api_key: str,
    ) -> None:
        """Background worker: fetch rank data and post result to the main thread.

        Uses the 2-call primary path (fetch_entries/by-puuid).  Falls back to
        the 3-call chain if by-puuid returns 404 (defensive runtime guard per
        02-02-SUMMARY.md).

        All AppState mutations happen directly on the worker thread — they only
        mutate AppState + call _push_state (window.state), which is the
        pywebview-documented thread-safe channel (no tkinter main-thread needed).

        Args:
            username: Riot username — used to look up the account on main thread.
            puuid:    Account PUUID for the API call.
            region:   "EUW" or "EUNE".
            api_key:  API key — passed as a plain arg, never stored (T-02-09).
        """
        try:
            try:
                entries = rank_service.fetch_entries(puuid, region, api_key)
            except rank_service.RiotAPIError as primary_err:
                if primary_err.status_code == 404:
                    # Defensive fallback: 3-call chain (summoner-v4 → entries/by-summoner)
                    summoner_id = rank_service.fetch_summoner_id(puuid, region, api_key)
                    entries = rank_service.fetch_entries_by_summoner(
                        summoner_id, region, api_key
                    )
                else:
                    raise  # propagate non-404 errors (401/403/429/5xx)

            rank_info = rank_service.parse_entries(entries)
            # Direct call on worker thread — _on_rank_ready only mutates AppState
            # + calls _push_state (thread-safe pywebview channel; no tkinter needed).
            self._on_rank_ready(username, rank_info)
        except Exception as exc:  # noqa: BLE001
            # Direct call on worker thread — same pattern as _on_rank_ready.
            self._on_rank_error(username, exc)

    def _rank_info_to_dict(self, rank_info: RankInfo) -> dict:
        """Serialize a RankInfo to a JSON-safe dict for Account.rank_cache.

        Stores both queues as nested dicts (or None for unranked), plus
        the fetch timestamp and stale flag.
        """
        def _queue_to_dict(q):
            if q is None:
                return None
            return {
                "tier": q.tier,
                "division": q.division,
                "lp": q.lp,
                "wins": q.wins,
                "losses": q.losses,
            }

        return {
            "solo": _queue_to_dict(rank_info.solo),
            "flex": _queue_to_dict(rank_info.flex),
            "fetched_at": rank_info.fetched_at,
            "stale": rank_info.stale,
        }

    def _on_rank_ready(self, username: str, rank_info: RankInfo) -> None:
        """Handle a successful rank fetch (main thread only).

        Updates Account.rank_cache and rank_cache_ts, persists to disk, and
        notifies listeners so the GUI re-renders with fresh rank data (D-24).

        Args:
            username:  Riot username of the account whose rank was fetched.
            rank_info: Parsed RankInfo from rank_service.parse_entries.
        """
        for acc in self.state.accounts:
            if acc.username == username:
                acc.rank_cache = self._rank_info_to_dict(rank_info)
                acc.rank_cache_ts = rank_info.fetched_at
                break
        config.save_state(self.state)
        # Plan 08-04 (D-09): a successful fetch proves the key is valid again —
        # clear the persistent header hint set by a prior 401/403.
        self._api_key_warning = False
        self._push_state()

    def _on_rank_error(self, username: str, exc: Exception) -> None:
        """Handle a failed rank fetch (main thread only).

        D-28: If the account has a cached rank, keeps it and marks it stale
        (``rank_cache["stale"] = True``).  If no cache exists, sets a failure
        marker so the card can display "Rang: Laden fehlgeschlagen".

        T-02-12: For 401/403 errors, posts a user-friendly key-invalid message
        to the status bar without including the key value.

        Args:
            username: Riot username of the account whose fetch failed.
            exc:      The exception that caused the failure.
        """
        for acc in self.state.accounts:
            if acc.username == username:
                if acc.rank_cache is not None:
                    # Keep existing cache, mark stale (D-28)
                    acc.rank_cache["stale"] = True
                else:
                    # No cache — set failure marker so card can show placeholder
                    acc.rank_cache = {"stale": True, "failed": True}
                break

        # WR-06: persist the stale/failure flag so it survives a restart,
        # mirroring _on_rank_ready (otherwise the on-disk cache keeps stale=False).
        config.save_state(self.state)

        # T-02-12: surface key-invalid message on 401/403 only (key value never included)
        if isinstance(exc, rank_service.RiotAPIError) and exc.status_code in (401, 403):
            # Plan 08-05 (ONBOARD-04): set the key+params contract directly
            # (not via _set_status, which would also change SwitchStatus —
            # rank errors stay silent per UI-SPEC) so window.state.status_key
            # AND status_message both reflect the current-language text.
            self._status_key = "status.api_key_invalid"
            self._status_params = {}
            self.state.status_message = i18n.t("status.api_key_invalid")
            # Note: we do NOT change SwitchStatus here (rank errors are silent per UI-SPEC)
            # Plan 08-04 (D-09): also set the PERSISTENT header hint (unlike
            # status_message, which is transient and gets overwritten by other
            # status updates) — cleared on the next successful _on_rank_ready.
            self._api_key_warning = True

        self._push_state()

    def _schedule_rank_refresh_timer(self) -> None:
        """Trigger an immediate rank refresh and schedule the next one in 15 min.

        Called from the pywebviewready bridge (on_webview_ready) to kick off
        D-23 Trigger 1 (app start) and Trigger 4 (15-min repeating timer).

        WR-03: guarded by _shutting_down; threading.Timer daemon stops on shutdown.
        """
        if self._shutting_down:
            return
        self._trigger_rank_refresh()
        self._start_timer(
            RANK_REFRESH_INTERVAL_MS / 1000, self._schedule_rank_refresh_timer
        )

    def _start_game_end_polling(self) -> None:
        """Start polling for game end after a successful switch (D-23 Trigger 3).

        Schedules the first poll with a 5 s daemon timer.  Each poll
        re-schedules itself until the game is no longer running, then fires
        a rank refresh.
        """
        if self._shutting_down:
            return
        self._start_timer(5, self._check_game_ended)

    def _check_game_ended(self) -> None:
        """Poll whether the League game has ended; trigger rank refresh when done.

        Called by the daemon timer started in ``_start_game_end_polling``.
        Stops the poll loop when the game is no longer running and fires
        ``_trigger_rank_refresh`` (D-23 Trigger 3).

        WR-03: guarded by _shutting_down — timer is not rescheduled after shutdown.
        """
        if self._shutting_down:
            return
        if not riot_client.is_game_running():
            self._trigger_rank_refresh()
        else:
            self._start_timer(5, self._check_game_ended)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _set_status(self, status: SwitchStatus, key: str, **params) -> None:
        """Update AppState status and message (key+params contract), then push to JS.

        Plan 08-05 (ONBOARD-04, D-15/D-16): *key* is a dotted ``strings.json``
        key (e.g. ``"status.killing_client"``), not pre-formatted text.
        Resolves the current-language text via ``gui.i18n.t()`` into
        ``state.status_message`` for any legacy direct reader, while
        ``_status_key``/``_status_params`` (mirrored to ``window.state`` by
        ``_push_state``) let ``app.js`` re-resolve the very same key live
        when the user switches languages — no restart needed.

        Thread-safe: only mutates AppState + calls _push_state (window.state),
        the pywebview-documented cross-thread channel.  May be called from any
        thread.

        Args:
            status: The new ``SwitchStatus`` enum value.
            key:    Dotted ``strings.json`` key (e.g. "status.killing_client"),
                    or "" for no message.
            **params: Values substituted into the resolved template's
                    ``{placeholder}``s (e.g. ``name=target.display_name``).
        """
        self.state.status = status
        self._status_key = key
        self._status_params = params
        self.state.status_message = i18n.t(key, **params)
        self._push_state()

    def _post_status(self, key: str, **params) -> None:
        """Post a SWITCHING status update (key+params) from a background thread.

        Direct call — no main-thread dispatch needed (window.state is the
        cross-thread channel, no tkinter required).

        Args:
            key: Dotted ``strings.json`` key for the step text.
            **params: Values substituted into the resolved template.
        """
        self._set_status(SwitchStatus.SWITCHING, key, **params)

    def _post_error(self, key: str, **params) -> None:
        """Post an ERROR status update (key+params) from a background thread.

        Direct call — no main-thread dispatch needed.

        Args:
            key: Dotted ``strings.json`` key for the error text.
            **params: Values substituted into the resolved template.
        """
        self._set_status(SwitchStatus.ERROR, key, **params)
