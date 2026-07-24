"""Unit tests for gui/js_api.py — D-09 toggle_max, D-10 resize_to, D-19 recapture_session.

Uses a minimal FakeWindow stub (no real pywebview) consistent with the pattern
in tests/test_controller_decouple.py.
"""
from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Minimal stub — mirrors the pywebview Window surface used by JsApi
# ---------------------------------------------------------------------------

class _FakeWindow:
    """Fake pywebview Window that records chrome calls."""

    def __init__(self) -> None:
        self.maximize_calls: int = 0
        self.restore_calls: int = 0
        self.resize_calls: list[tuple[int, int]] = []

    def maximize(self) -> None:
        self.maximize_calls += 1

    def restore(self) -> None:
        self.restore_calls += 1

    def resize(self, width: int, height: int) -> None:
        self.resize_calls.append((width, height))

    def minimize(self) -> None:
        pass

    def destroy(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_api(window: _FakeWindow | None = None):
    """Return a bound JsApi instance with a fake window (and no controller)."""
    # Import fresh each time to avoid cross-test state leakage
    import importlib
    # Ensure a clean import if already cached
    if "gui.js_api" in sys.modules:
        importlib.reload(sys.modules["gui.js_api"])
    from gui.js_api import JsApi
    api = JsApi()
    api._window = window if window is not None else _FakeWindow()
    return api


# ---------------------------------------------------------------------------
# D-09 toggle_max tests
# ---------------------------------------------------------------------------

class TestToggleMax(unittest.TestCase):
    """JsApi.toggle_max() must toggle between maximize and restore (D-09)."""

    def test_first_call_maximizes(self) -> None:
        """First toggle_max call must call window.maximize()."""
        w = _FakeWindow()
        api = _make_api(w)
        api.toggle_max()
        self.assertEqual(w.maximize_calls, 1)
        self.assertEqual(w.restore_calls, 0)

    def test_first_call_returns_maximized_true(self) -> None:
        """First toggle_max must return {'maximized': True}."""
        api = _make_api()
        result = api.toggle_max()
        self.assertIsInstance(result, dict)
        self.assertIn("maximized", result)
        self.assertTrue(result["maximized"])

    def test_second_call_restores(self) -> None:
        """Second toggle_max call must call window.restore(), not maximize again."""
        w = _FakeWindow()
        api = _make_api(w)
        api.toggle_max()   # maximise
        api.toggle_max()   # restore
        self.assertEqual(w.maximize_calls, 1, "maximize() should have been called exactly once")
        self.assertEqual(w.restore_calls, 1, "restore() should have been called exactly once")

    def test_second_call_returns_maximized_false(self) -> None:
        """Second toggle_max must return {'maximized': False}."""
        api = _make_api()
        api.toggle_max()   # maximise
        result = api.toggle_max()  # restore
        self.assertIsInstance(result, dict)
        self.assertFalse(result["maximized"])

    def test_three_calls_toggle_correctly(self) -> None:
        """Three consecutive calls must follow max → restore → max pattern."""
        w = _FakeWindow()
        api = _make_api(w)
        r1 = api.toggle_max()   # max
        r2 = api.toggle_max()   # restore
        r3 = api.toggle_max()   # max again
        self.assertTrue(r1["maximized"])
        self.assertFalse(r2["maximized"])
        self.assertTrue(r3["maximized"])
        self.assertEqual(w.maximize_calls, 2)
        self.assertEqual(w.restore_calls, 1)

    def test_initial_state_is_not_maximized(self) -> None:
        """_maximized must start False before any call."""
        from gui.js_api import JsApi
        api = JsApi()
        self.assertFalse(api._maximized)


# ---------------------------------------------------------------------------
# D-10 resize_to tests
# ---------------------------------------------------------------------------

class TestResizeTo(unittest.TestCase):
    """JsApi.resize_to() must clamp to min (480, 400) and delegate to window.resize (D-10)."""

    def test_resize_passes_through_valid_size(self) -> None:
        """resize_to with size above min must pass exact values to window.resize."""
        w = _FakeWindow()
        api = _make_api(w)
        api.resize_to(800, 600)
        self.assertEqual(w.resize_calls, [(800, 600)])

    def test_resize_clamps_width_below_min(self) -> None:
        """resize_to with width < 480 must clamp width to 480."""
        w = _FakeWindow()
        api = _make_api(w)
        api.resize_to(300, 600)
        self.assertEqual(w.resize_calls, [(480, 600)])

    def test_resize_clamps_height_below_min(self) -> None:
        """resize_to with height < 400 must clamp height to 400."""
        w = _FakeWindow()
        api = _make_api(w)
        api.resize_to(800, 200)
        self.assertEqual(w.resize_calls, [(800, 400)])

    def test_resize_clamps_both_below_min(self) -> None:
        """resize_to with both dimensions below min must clamp both."""
        w = _FakeWindow()
        api = _make_api(w)
        api.resize_to(1, 1)
        self.assertEqual(w.resize_calls, [(480, 400)])

    def test_resize_exact_min_values(self) -> None:
        """resize_to with exactly min dimensions must pass through unchanged."""
        w = _FakeWindow()
        api = _make_api(w)
        api.resize_to(480, 400)
        self.assertEqual(w.resize_calls, [(480, 400)])

    def test_resize_returns_none(self) -> None:
        """resize_to must return None (fire-and-forget bridge method)."""
        api = _make_api()
        result = api.resize_to(800, 600)
        self.assertIsNone(result)

    def test_resize_converts_to_int(self) -> None:
        """resize_to must pass integer values to window.resize (guards float inputs)."""
        w = _FakeWindow()
        api = _make_api(w)
        api.resize_to(820.7, 780.3)
        width, height = w.resize_calls[0]
        self.assertIsInstance(width, int)
        self.assertIsInstance(height, int)
        self.assertEqual(width, 820)
        self.assertEqual(height, 780)


# ---------------------------------------------------------------------------
# D-19 recapture_session bridge tests (SESSION-01)
# ---------------------------------------------------------------------------

class TestRecaptureSession(unittest.TestCase):
    """JsApi.recapture_session must be a one-line delegate (T-04-05)."""

    def test_delegates_to_controller_with_username(self) -> None:
        """recapture_session forwards the username to controller.recapture_session."""
        from gui.js_api import JsApi
        api = JsApi()
        fake_controller = MagicMock()
        api._controller = fake_controller
        api.recapture_session("riotuser1")
        fake_controller.recapture_session.assert_called_once_with("riotuser1")

    def test_returns_none(self) -> None:
        """recapture_session returns None — never a path/session content (D-22)."""
        from gui.js_api import JsApi
        api = JsApi()
        api._controller = MagicMock()
        result = api.recapture_session("riotuser1")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
