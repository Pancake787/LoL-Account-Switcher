"""core.py — GUI-freie Switch-Sequenz fuer LoL Account Switcher.

Stellt perform_switch(username) -> SwitchResult bereit, die vollstaendig
unabhaengig von tkinter/customtkinter laeuft. Wird von GUI (controller.py)
und CLI (main.py headless-Pfad) geteilt.

Anti-Pattern-Guard (D-30 / CONTEXT.md Pitfall 2):
  Verboten auf Modulebene und ueberall in dieser Datei:
    - import tkinter
    - import customtkinter
    - import threading
    - import rank_service
    - import credential_store
"""
from __future__ import annotations

from enum import Enum

import config
import riot_client


class SwitchResult(Enum):
    """Ergebnis eines perform_switch-Aufrufs.

    Jeder Wert entspricht einem bestimmten Abbruch- oder Erfolgszustand
    im Kill -> Swap -> Start Ablauf (D-30).
    """

    SUCCESS = "success"
    BLOCKED = "blocked"          # Match laeuft (D-30a) — Hard-Block, kein Override
    NO_SNAPSHOT = "no_snapshot"  # D-34: kein Snapshot fuer diesen Account vorhanden
    RIOT_NOT_FOUND = "riot_not_found"  # RiotClientServices.exe nicht gefunden
    STOP_FAILED = "stop_failed"  # riot_client.stop() hat timeout ueberschritten
    ERROR = "error"              # unerwartete Ausnahme


def validate_username(username: str) -> None:
    """Validiere den Benutzernamen am Trust-Boundary (T-03-01 / CR-01).

    Der username wird als Verzeichnisname (``SESSIONS_DIR / username``) und als
    WCM-Key verwendet. Pathlib-Join-Semantik macht einen Pfadtrenner, eine
    Parent-Ref (``..``) oder einen Laufwerks-/UNC-Praefix gefaehrlich: er laesst
    den Join aus SESSIONS_DIR ausbrechen und damit ``os.replace`` (swap_session)
    bzw. ``shutil.rmtree`` (delete_account) auf ein beliebiges Ziel zeigen.
    Diese Funktion weist solche Eingaben ab, BEVOR irgendein Pfad-Join passiert.

    Args:
        username: Der zu pruefende Riot-Benutzername.

    Raises:
        ValueError: Bei leerem/whitespace-umrahmten username oder wenn er
                    Pfadtrenner, NUL, eine Parent-Ref enthaelt oder das
                    Sessions-Verzeichnis verlaesst.
    """
    if not username or username != username.strip():
        raise ValueError("Ungueltiger Benutzername.")
    if "\0" in username or "/" in username or "\\" in username or ".." in username:
        raise ValueError("Benutzername darf keine Pfadtrenner enthalten.")
    # Belt-and-suspenders: der resolvte Join muss innerhalb SESSIONS_DIR bleiben.
    base = config.SESSIONS_DIR.resolve()
    target = (base / username).resolve()
    if target != base and base not in target.parents:
        raise ValueError("Benutzername verlaesst das Sessions-Verzeichnis.")


def perform_switch(username: str) -> SwitchResult:
    """GUI-freie Switch-Sequenz: Kill -> Swap -> Start.

    Laedt den State bei jedem Aufruf frisch (kein Singleton-State), damit
    parallele Aufrufe von CLI und GUI sich nicht gegenseitig stoeren.

    Seiteneffekte (D-30b):
      - Persistiert active_username NICHT. Das macht der Aufrufer (main.py).
      - Ruft niemals save_state auf (D-30b — Persistenz ist Aufgabe des Aufrufers).

    Match-Guard (D-30a):
      Als erste Anweisung wird geprueft, ob League of Legends.exe laeuft.
      Bei einem laufenden Match wird sofort BLOCKED zurueckgegeben —
      kein Kill, kein Swap, kein Start.

    Args:
        username: Riot-Benutzername des Ziel-Accounts (unveraenderlicher
                  Identifier, wird an riot_client.swap_session uebergeben).
                  Darf KEIN absoluter Pfad sein (T-03-01).

    Returns:
        SwitchResult-Enum-Wert gemaess erstem eingetretenen Zustand.
    """
    # Match-Guard (D-30a) — ERSTE Anweisung, kein Override
    if riot_client.is_game_running():
        return SwitchResult.BLOCKED

    try:
        # T-03-01 / CR-01: username VOR jedem Pfad-Join validieren. Eine
        # ValueError wird vom aeusseren except zu SwitchResult.ERROR gemappt,
        # sodass weder swap_session (os.replace) noch ein Snapshot-Pfad mit
        # einem traversierenden username erreicht werden.
        validate_username(username)

        # No-destructive-failure (D-34): Snapshot-Existenz VOR dem Beenden pruefen.
        # Ohne Snapshot ist der Switch nicht durchfuehrbar — die laufende Riot/League-
        # Session darf dann NICHT beendet werden (sonst stuende der User ohne Client
        # UND ohne Switch da). Frueher lag diese Pruefung erst nach stop()/swap_session,
        # was eine laufende Session unnoetig beendete.
        if not riot_client.snapshot_exists(username):
            return SwitchResult.NO_SNAPSHOT

        # Frischer State-Read pro Aufruf (kein Singleton-State — CLI + GUI koennen parallel laufen)
        state = config.load_state()
        active_username = state.active_username
        snapshot_usernames = {a.username for a in state.accounts if a.has_snapshot}

        # Step 0 (best-effort): Snapshot des ausgehenden Accounts refreshen,
        # damit der aktuellste Live-Token gesichert wird bevor der Client beendet wird.
        # Eine Exception hier bricht den Switch NICHT ab (D-12 / Pitfall 2).
        if (
            active_username is not None
            and active_username != username
            and active_username in snapshot_usernames
        ):
            try:
                riot_client.refresh_snapshot(active_username)
            except Exception:  # noqa: BLE001
                pass  # best-effort — Switch nie abbrechen

        # Step 1: Alle Riot/League-Prozesse beenden und auf Tod warten
        # riot_client.stop() enthaelt bereits Poll-until-dead (CRIT-1)
        if not riot_client.stop(timeout=10.0):
            return SwitchResult.STOP_FAILED

        # Step 2: Session-Datei tauschen (atomic via staging + os.replace)
        # FileNotFoundError = kein Snapshot vorhanden (D-34)
        # In core.py kein First-Login-Flow (kein GUI) — nur SwitchResult.NO_SNAPSHOT
        try:
            riot_client.swap_session(username)
        except FileNotFoundError:
            return SwitchResult.NO_SNAPSHOT  # D-34

        # Step 3: Riot Client starten
        riot_exe = riot_client.find_riot_client_exe()
        if riot_exe is None:
            return SwitchResult.RIOT_NOT_FOUND
        riot_client.start(riot_exe)

        return SwitchResult.SUCCESS

    except Exception:  # noqa: BLE001
        # Unerwartete Ausnahme — sicher zurueckgeben statt crash
        return SwitchResult.ERROR
