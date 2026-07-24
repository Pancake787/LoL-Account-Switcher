"""LoL Account Switcher — Entry point.

Startup sequence (GUI-Pfad):
1. Assert that the Windows Credential Manager keyring backend is active.
2. Construct Controller(root=None) — the pywebview Window is injected inside
   webview_window.start() before the event loop starts.
3. Call gui.webview_window.start(controller) — blocks until the window closes.
   JsApi.close() calls controller.shutdown() before destroying the window.

Headless CLI-Pfad (D-29 / SD-02):
  lolswitcher switch <username>
  -> core.perform_switch(username)
  -> EXIT 0 on SUCCESS (+ persists active_username, D-30b)
  -> EXIT 1 on any error (D-31)
  -> Importiert KEIN webview/customtkinter/tkinter (SD-07 / Anti-Pattern-Guard)

Pitfall (RESEARCH.md Pitfall 1): PyInstaller --noconsole setzt sys.stdout=None.
  Der headless-Pfad darf KEIN print() und KEIN sys.exit("string") nutzen.
  Nur sys.exit(0) / sys.exit(1) (Integer-Exit-Codes).
"""

from __future__ import annotations

import argparse
import sys


def _assert_secure_keyring() -> None:
    """Ensure the Windows Credential Manager keyring backend is active.

    Exits the process with a German error message if a plaintext fallback
    backend is detected.  This must be called before any credential operation
    or UI is shown.

    Security: Prevents silent plaintext storage if keyring falls back to a
    less-secure backend (e.g. on a system where pywin32 is missing).

    NOTE: keyring is imported LOCALLY here (not on module-level) so the headless
    switch-Pfad never triggers this import (GUI-only, Anti-Pattern-Guard D-30/SD-07).
    """
    import keyring  # noqa: E402 — local import, GUI-only

    backend = keyring.get_keyring()

    # Prefer an exact type check against the real Windows Credential Manager
    # backend (WinVaultKeyring).  This deterministically rejects the chainer
    # selector and any third-party class that merely contains "Windows" in its
    # name yet stores plaintext (WR-06).  If the backend module cannot be
    # imported for some reason, fall back to the substring name check rather
    # than weakening the guarantee — we must still fail closed.
    is_secure: bool
    try:
        from keyring.backends import Windows as _win_backend  # type: ignore

        is_secure = isinstance(backend, _win_backend.WinVaultKeyring)
    except Exception:  # noqa: BLE001 — import/availability is best-effort
        backend_name = backend.__class__.__name__
        is_secure = "WinVault" in backend_name or "Windows" in backend_name

    if not is_secure:
        sys.exit(
            "Sicherheitsfehler: Windows Credential Manager ist nicht verfügbar. "
            "App kann nicht gestartet werden."
        )


def main() -> None:
    """Application entry point.

    Subcommand-Check laeuft VOR allen GUI-Imports (A7: parse_known_args
    vermeidet SystemExit ohne Subcommand, RESEARCH.md).
    """
    # ------------------------------------------------------------------
    # Subcommand-Check VOR GUI-Imports (RESEARCH.md Pattern 4 + A7)
    # ------------------------------------------------------------------
    parser = argparse.ArgumentParser(prog="lolswitcher", add_help=False)
    sub = parser.add_subparsers(dest="command")
    sw = sub.add_parser("switch")
    sw.add_argument("username")
    sk = sub.add_parser("set-api-key")
    sk.add_argument("key", nargs="?")  # optional: prompt (hidden) when omitted
    args, _ = parser.parse_known_args()  # parse_known_args: kein SystemExit ohne Args

    if args.command == "set-api-key":
        # ------------------------------------------------------------------
        # Headless: persist the Riot API key DPAPI-encrypted to config.APP_DIR.
        # Key may be passed as an argument or entered at a hidden prompt — it is
        # never printed back. No GUI import (Anti-Pattern-Guard SD-07 / D-30).
        # ------------------------------------------------------------------
        import credential_store  # noqa: E402

        key = (args.key or "").strip()
        if not key:
            import getpass  # noqa: E402
            try:
                key = getpass.getpass("Riot API Key: ").strip()
            except (EOFError, OSError):
                key = ""  # no console (e.g. windowed exe double-clicked)
        if not key:
            sys.exit(1)  # nothing to store
        try:
            credential_store.set_api_key_file(key)
        except OSError:
            sys.exit(1)
        sys.exit(0)

    if args.command == "switch":
        # ------------------------------------------------------------------
        # Headless-Pfad — KEIN webview/customtkinter/tkinter-Import
        # (Anti-Pattern-Guard SD-07 / D-30 / CONTEXT.md Pitfall 2)
        # ------------------------------------------------------------------
        from core import perform_switch, SwitchResult  # noqa: E402
        import config  # noqa: E402

        result = perform_switch(args.username)

        if result == SwitchResult.SUCCESS:
            # D-30b: CLI persistiert active_username selbst (core macht das NICHT).
            # CR-02: nur persistieren, wenn der username tatsaechlich einem
            # vorhandenen Account entspricht (kein Bogus-Marker), UND ein
            # Persistenz-Fehler (Disk voll/Rechte) darf den bereits erfolgten
            # Switch NICHT als Fehlschlag melden — der Marker ist best-effort.
            state = config.load_state()
            if any(a.username == args.username for a in state.accounts):
                state.active_username = args.username
                try:
                    config.save_state(state)
                except OSError:
                    pass  # Switch ist bereits erfolgt; Marker ist best-effort
            sys.exit(0)
        else:
            # D-31: jeder Fehler (BLOCKED/NO_SNAPSHOT/STOP_FAILED/RIOT_NOT_FOUND/ERROR)
            # -> non-zero Exit-Code.
            # KEIN print() — --noconsole setzt sys.stdout=None (Pitfall 1)
            # KEIN sys.exit("string") — Integer-Exit-Code only
            sys.exit(1)

    # ------------------------------------------------------------------
    # GUI-Pfad — Imports erst hier (nach dem switch-Branch)
    # webview/Controller nur bei Bedarf geladen (SD-07 / GUI-09)
    # ------------------------------------------------------------------
    _assert_secure_keyring()

    from controller import Controller  # noqa: E402
    from gui.webview_window import start as _start_webview  # noqa: E402

    # Controller loads AppState from disk; the pywebview Window is injected
    # inside _start_webview() before the event loop starts (D-15 / GUI-07).
    controller = Controller(root=None)

    # Blocks until the window closes.  JsApi.close() calls controller.shutdown()
    # before destroying the pywebview window (WR-03).
    _start_webview(controller)


if __name__ == "__main__":
    main()
