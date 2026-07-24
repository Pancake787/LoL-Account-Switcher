"""Unit tests for gui/js_api.py — D-09 toggle_max, D-10 resize_to, D-19 recapture_session.

Uses a minimal FakeWindow stub (no real pywebview) consistent with the pattern
in tests/test_controller_decouple.py.
"""
from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock, patch


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


# ---------------------------------------------------------------------------
# D-11 region-badge data source (Plan 08-03 Task 2): the card() region badge
# reads acc.region from the controller-serialized account dict — verify it
# is present and holds a canonical platform id.
# ---------------------------------------------------------------------------

class _FakeControllerWindowState:
    """Minimal pywebview window.state stub — supports attribute assignment."""

    def __init__(self):
        self.accounts = None
        self.active_username = None
        self.status = None
        self.status_message = None
        self.pending_first_login = None


class _FakeControllerWindow:
    """Minimal pywebview Window stub (mirrors test_controller_decouple.FakeWindow)."""

    def __init__(self):
        self.state = _FakeControllerWindowState()

    def minimize(self):
        pass

    def maximize(self):
        pass

    def destroy(self):
        pass


class TestSerializeAccountsRegionField(unittest.TestCase):
    """_serialize_accounts() must expose a canonical `region` value for every
    account — the sole data source for app.js's region-badge (D-11)."""

    def _make_controller(self, region: str):
        import importlib
        from unittest.mock import patch
        import controller as ctrl_module
        from models import Account, AppState

        window = _FakeControllerWindow()
        with patch("config.load_state") as mock_load, \
             patch("config.ensure_dirs"), \
             patch.object(ctrl_module.Controller, "_schedule_accounts_poll"):
            mock_load.return_value = AppState(accounts=[
                Account(
                    username="kruser",
                    display_name="KR Smurf",
                    has_snapshot=False,
                    region=region,
                ),
            ])
            return ctrl_module.Controller(root=window)

    def test_serialized_region_is_present_and_canonical(self) -> None:
        """A KR account serializes with region="KR" — a real PLATFORM_TO_REGIONAL key."""
        import rank_service
        ctrl = self._make_controller("KR")
        result = ctrl._serialize_accounts()
        self.assertEqual(len(result), 1)
        self.assertIn("region", result[0])
        self.assertEqual(result[0]["region"], "KR")
        self.assertIn(result[0]["region"], rank_service.PLATFORM_TO_REGIONAL)

    def test_serialized_region_survives_for_migrated_euw1_account(self) -> None:
        """A migrated EUW1 account (post config._normalize_region) still serializes
        its canonical region unchanged — no regression for existing users (D-12)."""
        import rank_service
        ctrl = self._make_controller("EUW1")
        result = ctrl._serialize_accounts()
        self.assertEqual(result[0]["region"], "EUW1")
        self.assertIn(result[0]["region"], rank_service.PLATFORM_TO_REGIONAL)


# ---------------------------------------------------------------------------
# Plan 08-04 Task 2 — Bridge methods: save_api_key, get_api_key_masked,
# delete_api_key, set_gpu, get_settings, open_external_url.
# ---------------------------------------------------------------------------

class TestSaveApiKeyBridge(unittest.TestCase):
    """JsApi.save_api_key wraps controller.save_api_key's ValueError as an ok-dict."""

    def test_returns_ok_true_on_success(self) -> None:
        from gui.js_api import JsApi
        api = JsApi()
        api._controller = MagicMock()
        result = api.save_api_key("RGAPI-some-key")
        self.assertEqual(result, {"ok": True})
        api._controller.save_api_key.assert_called_once_with("RGAPI-some-key")

    def test_returns_ok_false_with_error_on_value_error(self) -> None:
        from gui.js_api import JsApi
        api = JsApi()
        api._controller = MagicMock()
        api._controller.save_api_key.side_effect = ValueError("API-Key ungültig oder abgelaufen.")
        result = api.save_api_key("RGAPI-bad-key")
        self.assertEqual(result["ok"], False)
        self.assertEqual(result["error"], "API-Key ungültig oder abgelaufen.")

    def test_result_never_contains_key_value(self) -> None:
        """Neither the ok=True nor the ok=False result dict ever echoes the key."""
        from gui.js_api import JsApi
        api = JsApi()
        api._controller = MagicMock()
        secret = "RGAPI-super-secret-abcdef"
        result_ok = api.save_api_key(secret)
        self.assertNotIn(secret, str(result_ok))

        api._controller.save_api_key.side_effect = ValueError("ungültig")
        result_err = api.save_api_key(secret)
        self.assertNotIn(secret, str(result_err))


class TestGetApiKeyMaskedBridge(unittest.TestCase):
    def test_delegates_to_controller(self) -> None:
        from gui.js_api import JsApi
        api = JsApi()
        api._controller = MagicMock()
        api._controller.get_api_key_masked.return_value = "••••••••"
        self.assertEqual(api.get_api_key_masked(), "••••••••")


class TestDeleteApiKeyBridge(unittest.TestCase):
    def test_delegates_and_returns_ok_true(self) -> None:
        from gui.js_api import JsApi
        api = JsApi()
        api._controller = MagicMock()
        result = api.delete_api_key()
        api._controller.delete_api_key.assert_called_once_with()
        self.assertEqual(result, {"ok": True})


class TestSetGpuBridge(unittest.TestCase):
    def test_delegates_to_controller(self) -> None:
        from gui.js_api import JsApi
        api = JsApi()
        api._controller = MagicMock()
        result = api.set_gpu(True)
        api._controller.set_gpu.assert_called_once_with(True)
        self.assertIsNone(result)


class TestGetSettingsBridge(unittest.TestCase):
    def test_delegates_to_controller(self) -> None:
        from gui.js_api import JsApi
        api = JsApi()
        api._controller = MagicMock()
        expected = {
            "has_api_key": True,
            "api_key_masked": "••••••••",
            "language": "de",
            "update_check_enabled": True,
            "disable_gpu": True,
        }
        api._controller.get_settings.return_value = expected
        self.assertEqual(api.get_settings(), expected)


class TestOpenExternalUrlBridge(unittest.TestCase):
    """T-08-13: https + host-allowlist gate — anything else is rejected."""

    def test_rejects_http_scheme(self) -> None:
        from gui.js_api import JsApi
        with patch("gui.js_api.webbrowser") as mock_browser:
            api = JsApi()
            result = api.open_external_url("http://developer.riotgames.com/")
            self.assertEqual(result, {"ok": False})
            mock_browser.open.assert_not_called()

    def test_rejects_non_allowlisted_host(self) -> None:
        from gui.js_api import JsApi
        with patch("gui.js_api.webbrowser") as mock_browser:
            api = JsApi()
            result = api.open_external_url("https://evil.test/phish")
            self.assertEqual(result, {"ok": False})
            mock_browser.open.assert_not_called()

    def test_allows_developer_riotgames_com(self) -> None:
        from gui.js_api import JsApi
        with patch("gui.js_api.webbrowser") as mock_browser:
            api = JsApi()
            result = api.open_external_url("https://developer.riotgames.com/")
            self.assertEqual(result, {"ok": True})
            mock_browser.open.assert_called_once()

    def test_allows_github_com(self) -> None:
        from gui.js_api import JsApi
        with patch("gui.js_api.webbrowser") as mock_browser:
            api = JsApi()
            result = api.open_external_url(
                "https://github.com/Pancake787/LoL-Account-Switcher/releases/latest"
            )
            self.assertEqual(result, {"ok": True})
            mock_browser.open.assert_called_once()

    def test_rejects_host_that_merely_contains_allowlisted_substring(self) -> None:
        """A host like 'github.com.evil.test' must NOT match the allowlist
        (naive substring/`endswith` traps — this checks true subdomain containment)."""
        from gui.js_api import JsApi
        with patch("gui.js_api.webbrowser") as mock_browser:
            api = JsApi()
            result = api.open_external_url("https://github.com.evil.test/x")
            self.assertEqual(result, {"ok": False})
            mock_browser.open.assert_not_called()

    def test_rejects_malformed_url(self) -> None:
        from gui.js_api import JsApi
        with patch("gui.js_api.webbrowser") as mock_browser:
            api = JsApi()
            result = api.open_external_url("not-a-url")
            self.assertEqual(result, {"ok": False})
            mock_browser.open.assert_not_called()

    def test_rejects_backslash_before_at_parser_confusion(self) -> None:
        """CR-01: `urlsplit` and the WHATWG URL Standard disagree on how a
        backslash before an `@` in the authority is parsed for https/http
        schemes. `urlsplit("https://evil.com\\@github.com/x").hostname` ==
        "github.com" (passing a naive allowlist check) while every real
        browser navigates to evil.com. Must be rejected outright."""
        from gui.js_api import JsApi
        with patch("gui.js_api.webbrowser") as mock_browser:
            api = JsApi()
            result = api.open_external_url("https://evil.com\\@github.com/malicious-path")
            self.assertEqual(result, {"ok": False})
            mock_browser.open.assert_not_called()

    def test_rejects_plain_userinfo_at_trick(self) -> None:
        """CR-01: plain userinfo form (`https://github.com@evil.com/x`) must
        also be rejected — the raw-string gate disallows any `@` at all."""
        from gui.js_api import JsApi
        with patch("gui.js_api.webbrowser") as mock_browser:
            api = JsApi()
            result = api.open_external_url("https://github.com@evil.com/x")
            self.assertEqual(result, {"ok": False})
            mock_browser.open.assert_not_called()

    def test_rejects_http_downgrade_of_allowlisted_host(self) -> None:
        """CR-01: the raw-string prefix gate requires an exact `https://`
        origin — a plain http:// URL to an otherwise-allowlisted host must
        still be rejected (covers the downgrade variant beyond the existing
        developer.riotgames.com http test)."""
        from gui.js_api import JsApi
        with patch("gui.js_api.webbrowser") as mock_browser:
            api = JsApi()
            result = api.open_external_url("http://github.com/x")
            self.assertEqual(result, {"ok": False})
            mock_browser.open.assert_not_called()

    def test_rejects_uppercase_scheme_case_trick(self) -> None:
        """CR-01: an uppercase/mixed-case scheme must not slip past the
        raw-string prefix gate (which is explicitly case-insensitive) nor
        the parsed-scheme check."""
        from gui.js_api import JsApi
        with patch("gui.js_api.webbrowser") as mock_browser:
            api = JsApi()
            result = api.open_external_url("HTTPS://evil.com\\@github.com/x")
            self.assertEqual(result, {"ok": False})
            mock_browser.open.assert_not_called()

    def test_allows_uppercase_https_scheme_for_allowlisted_host(self) -> None:
        """The case-insensitive prefix gate must still allow a legitimately
        uppercased scheme to an actually-allowlisted origin through."""
        from gui.js_api import JsApi
        with patch("gui.js_api.webbrowser") as mock_browser:
            api = JsApi()
            result = api.open_external_url("HTTPS://github.com/Pancake787/LoL-Account-Switcher")
            self.assertEqual(result, {"ok": True})
            mock_browser.open.assert_called_once()

    def test_rejects_whitespace_in_url(self) -> None:
        """CR-01: embedded whitespace/control characters (another class of
        parser-confusion vector across different URL parsers) must be
        rejected outright."""
        from gui.js_api import JsApi
        with patch("gui.js_api.webbrowser") as mock_browser:
            api = JsApi()
            result = api.open_external_url("https://github.com/\t@evil.com/x")
            self.assertEqual(result, {"ok": False})
            mock_browser.open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
