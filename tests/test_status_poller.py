"""Tests for status_poller.py — StatusPoller daemon (D-12/D-13)."""
from __future__ import annotations

import ast
import os
import unittest
from unittest.mock import patch

import status_poller


class TestModuleBoundary(unittest.TestCase):
    """D-12: status_poller.py must import only stdlib + riot_client."""

    def test_no_forbidden_imports_in_source(self):
        """Module source must not import controller/gui/credential_store."""
        # status_poller's own __file__ is authoritative regardless of test location
        path = status_poller.__file__
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        mods = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    mods.append(alias.name.split(".")[0])
        forbidden = {"controller", "gui", "credential_store"}
        self.assertFalse(forbidden & set(mods), f"Forbidden imports found: {mods}")


class TestStatusPollerTransitions(unittest.TestCase):
    """StatusPoller._loop fires on_change only on (client_running, game_live) transitions."""

    def test_on_change_fires_only_on_transition(self):
        calls: list = []
        # Sequence of (client_running, game_live) polls: two identical, then a
        # change, then identical again, then a second change.
        sequence = [
            (False, False),
            (False, False),  # unchanged — must NOT fire again
            (True, False),   # transition — must fire
            (True, False),   # unchanged — must NOT fire again
            (True, True),    # transition — must fire
        ]
        poller = status_poller.StatusPoller(on_change=lambda c, g: calls.append((c, g)))
        poller._running = True

        call_index = [0]

        def _fake_is_client_running():
            return sequence[call_index[0]][0]

        def _fake_is_game_running():
            return sequence[call_index[0]][1]

        def _fake_sleep(_interval):
            call_index[0] += 1
            if call_index[0] >= len(sequence):
                poller._running = False

        with patch.object(status_poller.riot_client, "is_client_running", _fake_is_client_running), \
             patch.object(status_poller.riot_client, "is_game_running", _fake_is_game_running), \
             patch.object(status_poller.time, "sleep", _fake_sleep):
            poller._loop()

        self.assertEqual(calls, [(False, False), (True, False), (True, True)])

    def test_stop_before_start_is_safe(self):
        """stop() must be safe to call before start() was ever invoked."""
        poller = status_poller.StatusPoller(on_change=lambda c, g: None)
        poller.stop()  # must not raise
        self.assertFalse(poller._running)

    def test_stop_is_idempotent(self):
        """Multiple stop() calls must not raise."""
        poller = status_poller.StatusPoller(on_change=lambda c, g: None)
        poller.stop()
        poller.stop()
        poller.stop()
        self.assertFalse(poller._running)

    def test_stop_causes_loop_to_exit(self):
        """Calling stop() causes _loop to exit (running flag observed False)."""
        poller = status_poller.StatusPoller(on_change=lambda c, g: None)
        poller._running = True

        iterations = [0]

        def _fake_sleep(_interval):
            iterations[0] += 1
            poller.stop()  # simulate stop() called from another thread mid-loop

        with patch.object(status_poller.riot_client, "is_client_running", lambda: False), \
             patch.object(status_poller.riot_client, "is_game_running", lambda: False), \
             patch.object(status_poller.time, "sleep", _fake_sleep):
            poller._loop()

        self.assertEqual(iterations[0], 1)

    def test_interval_s_is_five_seconds(self):
        """D-13: poll interval must be 5.0 seconds (sparsest option)."""
        self.assertEqual(status_poller.StatusPoller.INTERVAL_S, 5.0)


if __name__ == "__main__":
    unittest.main()
