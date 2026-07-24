"""status_poller.py — background status polling for STATUS-01.

Module boundary (mirrors rank_service.py / D-12): imports only stdlib +
riot_client. MUST NOT import gui/controller/credential_store.

Re-run any time: this module has no CLI entry point — it is imported and
wired by gui/webview_window.py at startup.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import riot_client


class StatusPoller:
    """Polls Riot Client / game-live status on a daemon thread every 5s (D-13).

    ``on_change(client_running: bool, game_live: bool)`` is invoked ONLY when
    the polled tuple changes from the previous poll — no per-tick callback
    when state is unchanged (avoids unnecessary window.state churn, T-05-05).
    """

    #: Poll interval in seconds — sparsest option to minimize idle CPU,
    #: important since --disable-gpu (software rendering) is already active (D-13).
    INTERVAL_S: float = 5.0

    def __init__(self, on_change: Callable[[bool, bool], None]) -> None:
        """Args:
            on_change: Callback invoked with (client_running, game_live) —
                only on transitions, never on an unchanged poll.
        """
        self._on_change = on_change
        self._running: bool = False
        self._last: tuple = (None, None)
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Spawn the daemon polling thread."""
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the polling loop.

        Idempotent — safe to call multiple times, and safe to call before
        start() was ever invoked. Causes _loop to exit after at most one
        more sleep interval (WR-03 — no reschedule after this call).
        """
        self._running = False

    def _loop(self) -> None:
        """Poll riot_client every INTERVAL_S seconds; fire on_change on transitions only."""
        while self._running:
            state = (riot_client.is_client_running(), riot_client.is_game_running())
            if state != self._last:
                self._last = state
                self._on_change(*state)
            time.sleep(self.INTERVAL_S)
