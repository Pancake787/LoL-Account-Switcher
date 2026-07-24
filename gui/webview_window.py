"""pywebview shell for LoL Account Switcher.

Bootstrap module that owns the GPU env-var, frameless window creation, asset
path resolution, and the WebView2-absent native MessageBox (D-15/GUI-07/GUI-08).

Critical ordering constraint (Pitfall 1 / GUI-07):
    The WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS env-var MUST be set BEFORE any
    ``import webview`` — WebView2 reads it at process initialisation, which
    happens during the first pywebview import.  All logic in this module that
    requires webview is placed after the late ``import webview`` block.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys


# ---------------------------------------------------------------------------
# Helpers that run before ``import webview``
# ---------------------------------------------------------------------------

def _read_disable_gpu_setting() -> bool:
    """Return True (GPU disabled) unless config explicitly sets disable_gpu: false.

    Reads ``accounts.json`` from the standard AppData location.  Uses only
    stdlib (json, os) — no config.py import which might pull in models etc.
    Defaults to True on any error or missing key (GUI-07: disabled by default).

    Returns:
        True  → set the --disable-gpu env-var (default / safe behaviour).
        False → user opted out; do NOT set the env-var.
    """
    try:
        app_dir = os.path.join(os.environ.get("APPDATA", ""), "LoLSwitcher")
        accounts_json = os.path.join(app_dir, "accounts.json")
        with open(accounts_json, encoding="utf-8") as fh:
            data = json.load(fh)
        # Explicit false is the only value that opts out
        return data.get("disable_gpu", True) is not False
    except Exception:
        return True


def _asset_root() -> str:
    """Return the absolute path to ``gui/assets/``.

    Handles both the development environment (plain Python) and PyInstaller
    frozen mode (``sys._MEIPASS``).

    Returns:
        Absolute path string pointing to the ``gui/assets/`` directory.
    """
    if getattr(sys, "frozen", False):
        # PyInstaller extracts package to sys._MEIPASS at runtime
        return os.path.join(sys._MEIPASS, "gui", "assets")  # type: ignore[attr-defined]
    # Dev mode: this file lives at gui/webview_window.py; assets/ is a sibling
    return os.path.join(os.path.dirname(__file__), "assets")


def _html_path() -> str:
    """Return the absolute path to ``gui/assets/index.html``.

    Returns:
        Absolute path string to index.html.
    """
    return os.path.join(_asset_root(), "index.html")


def _set_app_user_model_id() -> None:
    """Set a stable AppUserModelID so Windows groups/icons the taskbar entry (D-15).

    Must be called BEFORE ``webview.create_window(...)`` — Windows reads the
    process's AppUserModelID at window-creation time to decide taskbar
    grouping and which icon to display. Without a stable AUMID, Windows falls
    back to the generic Python launcher icon instead of this app's icon.

    Defensive: the AUMID is a non-secret, project-neutral identifier with no
    functional dependency for the app to run, so any failure here (e.g. on a
    non-Windows/edge context) is swallowed rather than raised.
    """
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "LoLAccountSwitcher.Desktop"
        )
    except Exception:
        pass


def _icon_path() -> str:
    """Return the absolute path to the bundled ``assets/icon.ico`` (D-15).

    Handles both the development environment (plain Python) and PyInstaller
    frozen mode (``sys._MEIPASS``) — mirrors the ``_asset_root()`` pattern but
    for the TOP-LEVEL ``assets/`` directory (not ``gui/assets/``).

    Returns:
        Absolute path string pointing to ``assets/icon.ico``.
    """
    if getattr(sys, "frozen", False):
        # PyInstaller extracts the icon.ico datas entry to sys._MEIPASS at runtime
        return os.path.join(sys._MEIPASS, "assets", "icon.ico")  # type: ignore[attr-defined]
    # Dev mode: this file lives at gui/webview_window.py; project-root assets/
    # is one level up from gui/.
    return os.path.join(os.path.dirname(__file__), "..", "assets", "icon.ico")


def _show_webview2_missing_dialog() -> None:
    """Show a native Windows MessageBoxW when WebView2 Runtime is absent (D-15/GUI-08).

    Uses only ctypes (stdlib) — does not require a working WebView2 Runtime,
    and customtkinter/tkinter has been removed in v2.0.  The dialog contains
    the literal download URL so users know where to get the runtime.

    This function has no dependency on ``webview`` and must remain importable
    even when pywebview fails to load.
    """
    msg = (
        "WebView2 Runtime nicht gefunden.\n\n"
        "Bitte WebView2 installieren:\n"
        "https://aka.ms/webview2\n\n"
        "Nach der Installation die App neu starten."
    )
    title = "LoL Account Switcher — WebView2 fehlt"
    ctypes.windll.user32.MessageBoxW(
        None,
        msg,
        title,
        0x10,  # MB_ICONERROR
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def start(controller) -> None:  # noqa: ANN001
    """Start the pywebview GUI.

    Called from ``main.py`` GUI branch.  Replaces the customtkinter
    ``MainWindow()`` / ``mainloop()`` path.

    Critical ordering (GUI-07):
        1. Read disable_gpu config (accounts.json, stdlib only).
        2. Set ``WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS`` env-var if enabled.
        3. THEN ``import webview`` — the env-var must already be present.
        4. Create the frameless window and start the event loop.

    Args:
        controller: A fully-initialised ``Controller`` instance.  Its
            ``_window`` attribute will be set here before the event loop
            starts so ``_push_state()`` has a valid target.
    """
    # Step 1 — read opt-out from config BEFORE importing webview
    if _read_disable_gpu_setting():
        os.environ.setdefault(
            "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
            "--disable-gpu --disable-gpu-compositing",
        )

    # Step 2 — import webview (late import; after env-var)
    try:
        import webview  # noqa: PLC0415 — intentional late import (GPU env-var must be first)
    except Exception:
        _show_webview2_missing_dialog()
        sys.exit(1)

    # Step 2b — set AppUserModelID BEFORE create_window (D-15 ordering requirement)
    _set_app_user_model_id()

    # Step 3 — import JsApi locally to keep the headless path import-free
    # (Pitfall 9 / GUI-09: no GUI import before CLI branch check in main.py;
    #  this module is itself only imported in the GUI branch, so the import
    #  chain is already safe, but keeping JsApi local makes it explicit).
    from gui.js_api import JsApi  # noqa: PLC0415

    api = JsApi()

    # Step 4 — create frameless window + start event loop
    try:
        window = webview.create_window(
            "LoL Account Switcher",
            url=_html_path(),
            width=820,
            height=780,
            frameless=True,
            easy_drag=False,          # use pywebview-drag-region CSS class (Pitfall 2)
            background_color="#0d0e15",
            js_api=api,
            min_size=(480, 400),      # D-10 minimum size
        )
        api.bind(controller, window)
        controller._window = window   # required by _push_state()

        # STATUS-01 (D-12/D-17): local import mirrors the JsApi local-import
        # pattern above (GUI-09 headless safety) — status_poller.py must not
        # be imported before this point in the headless CLI path.
        from status_poller import StatusPoller  # noqa: PLC0415

        poller = StatusPoller(on_change=controller.update_client_status)
        controller._status_poller = poller  # so shutdown() can stop it centrally (WR-03)
        poller.start()

        webview.start(http_server=True, icon=_icon_path())
    except OSError as exc:
        exc_str = str(exc)
        if "2" in exc_str or "WebView2" in exc_str or "EdgeUpdate" in exc_str:
            _show_webview2_missing_dialog()
            sys.exit(1)
        raise
