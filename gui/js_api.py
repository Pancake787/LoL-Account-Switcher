"""JS-to-Python bridge for LoL Account Switcher (pywebview).

``JsApi`` is the thin adapter that pywebview exposes to the WebView2/JavaScript
layer as ``window.pywebview.api``.  Every method here is a one-liner that
delegates to ``controller`` or ``window`` — NO business logic lives in this
class (T-04-05 bridge guard).

Path-separator and format validation for ``username`` and ``riot_id`` stays
inside the controller (``add_account``, ``set_riot_id``) and is not duplicated
or weakened here.

Two-phase initialisation pattern (same as v1.0 ``MainWindow.set_controller``):
    Phase 1 — ``__init__``:  instance created before the window exists.
    Phase 2 — ``bind(controller, window)``:  called from ``webview_window.py``
               after both the Controller and the pywebview Window are ready.
"""
from __future__ import annotations

import webbrowser
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from controller import Controller  # noqa: F401 — type hints only

#: Plan 08-04 (T-08-13): fixed host allowlist for open_external_url — only
#: https URLs to these two hosts may ever be opened in the default browser.
_EXTERNAL_URL_HOST_ALLOWLIST: frozenset[str] = frozenset(
    {"developer.riotgames.com", "github.com"}
)


class JsApi:
    """Thin JS-to-Python bridge.

    All public methods are callable from JavaScript via
    ``pywebview.api.<method>(...)``.  They return either ``None`` or a plain
    dict ``{"ok": bool, "error": str}`` so that Promises in JS always resolve
    (never reject) — the JS caller inspects ``result.ok`` to decide whether
    to display an error message in the UI.
    """

    def __init__(self) -> None:
        """Phase-1 initialisation — called before the window exists."""
        self._controller: "Controller | None" = None
        self._window = None  # pywebview Window
        self._maximized: bool = False  # D-09: toggle state for maximize/restore

    def bind(self, controller: "Controller", window) -> None:  # noqa: ANN001
        """Phase-2 init: store controller + window references.

        Called from ``gui/webview_window.py`` after both objects are created.

        Args:
            controller: The fully-initialised Controller instance.
            window:     The pywebview Window object returned by
                        ``webview.create_window()``.
        """
        self._controller = controller
        self._window = window

    # ------------------------------------------------------------------
    # Window chrome (GUI-02)
    # ------------------------------------------------------------------

    def minimize(self) -> None:
        """Minimise the window to the taskbar."""
        self._window.minimize()

    def toggle_max(self) -> dict:
        """Toggle between maximised and restored window states (D-09).

        Real maximize (D-09 / STYLE-REFERENCE.md): uses ``window.maximize()``
        which maximises to the work area (respects taskbar). Full-screen mode
        is intentionally avoided here — see STYLE-REFERENCE.md for rationale.

        Returns:
            ``{"maximized": bool}`` — the new maximised state so the JS caller
            can update the button glyph (square □ when restored, ❐ when maximised).
        """
        if self._maximized:
            self._window.restore()
        else:
            self._window.maximize()
        self._maximized = not self._maximized
        return {"maximized": self._maximized}

    def resize_to(self, width: int, height: int) -> None:
        """Resize the window to (width, height), clamped to the minimum (D-10).

        Called by the JS resize grips on pointermove.  Clamping is duplicated
        here Python-side (the JS already clamps, but defence-in-depth).

        Minimum matches ``min_size=(480, 400)`` in ``webview_window.py``.

        WR-05: an explicit resize (e.g. dragging a resize grip) inherently means
        the window is no longer in its maximised state, so reset ``_maximized``
        to False.  Without this, the mirror could go stale — a user who resizes
        while ``_maximized`` was True would need two ``toggle_max`` clicks (the
        first calling ``restore()`` on an already-restored window) before the
        maximise glyph and behaviour resynchronised.

        Args:
            width:  Desired width in logical pixels.
            height: Desired height in logical pixels.
        """
        self._maximized = False
        self._window.resize(int(max(width, 480)), int(max(height, 400)))

    def close(self) -> None:
        """Shut down the controller (stops all timers) and destroy the window."""
        self._controller.shutdown()
        self._window.destroy()

    # ------------------------------------------------------------------
    # Account actions (GUI-03, GUI-04, GUI-05, GUI-06)
    # ------------------------------------------------------------------

    def switch_account(self, username: str) -> None:
        """Start a switch to the named account.

        Silently ignores unknown usernames (guard against stale JS state).

        Args:
            username: The account's Riot username.
        """
        acc = self._get_account(username)
        if acc:
            self._controller.switch_account(acc)

    def add_account(
        self,
        display_name: str,
        username: str,
        password: str,
        riot_id: str = "",
        region: str = "EUW",
    ) -> dict:
        """Add a new account.

        Returns:
            ``{"ok": True}`` on success or
            ``{"ok": False, "error": "<message>"}`` on ``ValueError`` —
            validation error message is safe to display in the modal.
        """
        try:
            self._controller.add_account(
                display_name, username, password, riot_id or None, region
            )
            return {"ok": True}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    def delete_account(self, username: str) -> None:
        """Delete the named account (including WCM entry and snapshot).

        Args:
            username: The account's Riot username.
        """
        self._controller.delete_account(username)

    def rename_account(self, username: str, new_display_name: str) -> dict:
        """Rename the display name of an existing account.

        Args:
            username:         Riot username identifying the account.
            new_display_name: New display label (shown in the UI).

        Returns:
            ``{"ok": True}`` or ``{"ok": False, "error": "<message>"}``.
        """
        try:
            self._controller.rename_account(username, new_display_name)
            return {"ok": True}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    def reorder_accounts(self, new_order: list) -> None:
        """Persist a new account order after a SortableJS drag-and-drop (D-08).

        Args:
            new_order: List of username strings in the desired display order.
        """
        self._controller.reorder_accounts(new_order)

    def refresh_ranks(self) -> None:
        """Trigger a manual rank refresh for all accounts (GUI-06)."""
        self._controller.refresh_ranks()

    def copy_password(self, username: str) -> dict:
        """Copy the named account's password to the clipboard.

        Clipboard write + auto-clear timer are handled Python-side in the
        controller (D-20, T-04-02 match-before-clear).

        Args:
            username: Riot username whose password should be copied.

        Returns:
            ``{"ok": True}`` if the copy succeeded, ``{"ok": False}`` otherwise.
        """
        ok = self._controller.copy_password(username)
        return {"ok": ok}

    def confirm_first_login(self) -> None:
        """Confirm that the first login for a new account is complete (D-02)."""
        self._controller.confirm_first_login_snapshot()

    def cancel_first_login(self) -> None:
        """Cancel a pending first-login snapshot flow."""
        self._controller.cancel_first_login()

    def recapture_session(self, username: str) -> None:
        """Trigger a session re-capture for an existing account (D-19, SESSION-01).

        One-line delegate only (T-04-05) — never returns a file path, snapshot
        content, or any session-related data to JS.

        Args:
            username: The account's Riot username.
        """
        self._controller.recapture_session(username)

    def set_riot_id(
        self, username: str, riot_id: str, region: str = "EUW"
    ) -> dict:
        """Update the Riot ID (GameName#TagLine) and region for an account.

        Path-separator / format validation lives in ``controller.set_riot_id``;
        this bridge method only wraps the ValueError in an ok-dict.

        Args:
            username: Riot username identifying the account.
            riot_id:  New ``GameName#TagLine`` string, or empty to clear.
            region:   Server region (e.g. ``"EUW"``, ``"EUNE"``).

        Returns:
            ``{"ok": True}`` or ``{"ok": False, "error": "<message>"}``.
        """
        try:
            self._controller.set_riot_id(username, riot_id or None, region)
            return {"ok": True}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Plan 08-04 — Onboarding + Settings (ONBOARD-01/02)
    # ------------------------------------------------------------------

    def save_api_key(self, key: str) -> dict:
        """Save a candidate Riot API key after live validation (D-03/D-08).

        Thin ok-dict wrapper — all validation/persistence/refresh logic lives
        in ``controller.save_api_key``; this method never inspects or logs the
        key value (T-04-05 bridge guard).

        Args:
            key: The personal Riot API key entered by the user.

        Returns:
            ``{"ok": True}`` or ``{"ok": False, "error": "<message>"}``.
        """
        try:
            self._controller.save_api_key(key)
            return {"ok": True}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    def get_api_key_masked(self) -> str:
        """Return the fixed 8-bullet key mask, or '' if no key is stored.

        Returns:
            str: The masked key display value — never the raw key (T-08-11).
        """
        return self._controller.get_api_key_masked()

    def delete_api_key(self) -> dict:
        """Delete the stored Riot API key (WCM + DPAPI file).

        Returns:
            ``{"ok": True}`` — delete_api_key is idempotent and never raises.
        """
        self._controller.delete_api_key()
        return {"ok": True}

    def set_gpu(self, enabled: bool) -> None:
        """Persist the GPU-acceleration toggle (D-07 — effective after restart).

        Args:
            enabled: True to enable GPU acceleration.
        """
        self._controller.set_gpu(enabled)

    def get_settings(self) -> dict:
        """Return the current app-wide settings for the Settings modal.

        Returns:
            dict: See ``controller.get_settings`` — never includes the raw key.
        """
        return self._controller.get_settings()

    def open_external_url(self, url: str) -> dict:
        """Open a URL in the default browser, gated by an https + host allowlist.

        T-08-13: only ``https://`` URLs whose host is exactly
        ``developer.riotgames.com`` or ``github.com`` (or a subdomain thereof)
        are ever passed to ``webbrowser.open``; anything else is rejected
        without side effects. This is the ONLY validation this bridge method
        performs (T-04-05 — no other business logic).

        Args:
            url: The candidate URL to open.

        Returns:
            ``{"ok": True}`` if the URL was opened, ``{"ok": False}`` otherwise.
        """
        try:
            parts = urlsplit(url)
        except ValueError:
            return {"ok": False}
        if parts.scheme != "https" or not parts.hostname:
            return {"ok": False}
        host = parts.hostname.lower()
        allowed = host in _EXTERNAL_URL_HOST_ALLOWLIST or any(
            host.endswith(f".{allowed_host}")
            for allowed_host in _EXTERNAL_URL_HOST_ALLOWLIST
        )
        if not allowed:
            return {"ok": False}
        webbrowser.open(url, new=2)
        return {"ok": True}

    def on_webview_ready(self) -> None:
        """Called from the JS ``pywebviewready`` handler via ``setTimeout(..., 0)``.

        Pushes the initial AppState to the JS layer so the account list renders
        immediately on startup (instead of waiting for the first change event).

        Also starts the 15-min repeating rank-refresh daemon timer and the
        game-end poll (D-16 / D-23 Trigger 4) so automatic rank updates run
        throughout the session without manual interaction.

        Active Python→JS channel: ``window.state`` top-level reassignment
        (Plan 04-01 smoke result — validated; no evaluate_js fallback needed).
        """
        self._controller._push_state()
        self._controller._schedule_rank_refresh_timer()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_account(self, username: str):  # noqa: ANN202
        """Look up an Account by Riot username.

        Args:
            username: The Riot login username to find.

        Returns:
            The matching ``Account`` object, or ``None`` if not found.
        """
        for acc in self._controller.state.accounts:
            if acc.username == username:
                return acc
        return None
