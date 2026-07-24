"""Tests for API-key keyring wrappers in credential_store.py.

Uses a dict-backed fake keyring so tests never touch the real Windows
Credential Manager — same pattern as test_account_mgmt.py.

TDD RED: written before the implementation is added to credential_store.py.
"""
from __future__ import annotations

import pathlib
import sys
import types
import unittest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Fake keyring module — backed by a simple dict, never touches WCM
# Mirrors the _FakeKeyring in test_account_mgmt.py exactly.
# ---------------------------------------------------------------------------

class _FakeKeyringErrors:
    """Minimal stub of keyring.errors."""
    class PasswordDeleteError(Exception):
        pass


class _FakeKeyring:
    """In-memory keyring backend."""

    def __init__(self):
        self._store: dict[tuple[str, str], str] = {}
        self.errors = _FakeKeyringErrors()

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        key = (service, username)
        if key not in self._store:
            raise _FakeKeyringErrors.PasswordDeleteError(f"{service}/{username} not found")
        del self._store[key]

    def reset(self) -> None:
        self._store.clear()


_fake_keyring = _FakeKeyring()

# Inject fake keyring into sys.modules BEFORE importing credential_store
_keyring_mod = types.ModuleType("keyring")
_keyring_mod.set_password = _fake_keyring.set_password
_keyring_mod.get_password = _fake_keyring.get_password
_keyring_mod.delete_password = _fake_keyring.delete_password
_keyring_errors_mod = types.ModuleType("keyring.errors")
_keyring_errors_mod.PasswordDeleteError = _FakeKeyringErrors.PasswordDeleteError
_keyring_mod.errors = _keyring_errors_mod
sys.modules["keyring"] = _keyring_mod
sys.modules["keyring.errors"] = _keyring_errors_mod

import credential_store  # noqa: E402  (must come after fake inject)
import config  # noqa: E402
import rank_service  # noqa: E402
import controller as controller_module  # noqa: E402


# ---------------------------------------------------------------------------
# TestApiKeyStore — covers the five behaviors described in the plan
# ---------------------------------------------------------------------------

class TestApiKeyStore(unittest.TestCase):
    """Unit tests for the API-key storage wrappers in credential_store."""

    def setUp(self) -> None:
        """Reset fake keyring and redirect config.APP_DIR to a temp dir.

        Redirecting APP_DIR keeps these WCM round-trip tests hermetic: the DPAPI
        key file (`riot_api_key.dat`) lives under config.APP_DIR, so pointing it
        at an empty temp dir means get_api_key() never reads a real on-disk key
        and falls through to the (faked) Credential Manager.
        """
        import tempfile

        _fake_keyring.reset()
        self._orig_app_dir = config.APP_DIR
        self._tmp = pathlib.Path(tempfile.mkdtemp())
        config.APP_DIR = self._tmp

    def tearDown(self) -> None:
        config.APP_DIR = self._orig_app_dir
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    # 1. API_SERVICE constant is distinct from the account SERVICE
    # ------------------------------------------------------------------

    def test_api_service_constant_distinct(self) -> None:
        """API_SERVICE must equal 'lol-switcher-api' and differ from SERVICE."""
        self.assertEqual(credential_store.API_SERVICE, "lol-switcher-api")
        self.assertNotEqual(credential_store.API_SERVICE, credential_store.SERVICE)

    def test_api_username_constant(self) -> None:
        """API_USERNAME must equal 'riot_api_key'."""
        self.assertEqual(credential_store.API_USERNAME, "riot_api_key")

    # ------------------------------------------------------------------
    # 2. Basic store-and-retrieve round-trip
    # ------------------------------------------------------------------

    def test_save_then_get_returns_key(self) -> None:
        """save_api_key('abc') then get_api_key() must return 'abc'."""
        credential_store.save_api_key("abc")
        self.assertEqual(credential_store.get_api_key(), "abc")

    # ------------------------------------------------------------------
    # 3. get_api_key returns '' (empty string, not None) when absent
    # ------------------------------------------------------------------

    def test_get_api_key_returns_empty_when_absent(self) -> None:
        """get_api_key() must return '' (not None) when no key is stored."""
        result = credential_store.get_api_key()
        self.assertEqual(result, "")
        self.assertIsNotNone(result)

    # ------------------------------------------------------------------
    # 4. Second save overwrites first (delete-then-set, no duplicate error)
    # ------------------------------------------------------------------

    def test_save_twice_returns_second_key(self) -> None:
        """Calling save_api_key twice must leave only the second key (no Windows duplicate error)."""
        credential_store.save_api_key("first-key")
        credential_store.save_api_key("second-key")
        self.assertEqual(credential_store.get_api_key(), "second-key")

    # ------------------------------------------------------------------
    # 5. delete_api_key when absent does not raise
    # ------------------------------------------------------------------

    def test_delete_api_key_when_absent_does_not_raise(self) -> None:
        """delete_api_key() on an empty store must not raise any exception."""
        try:
            credential_store.delete_api_key()
        except Exception as exc:  # pylint: disable=broad-except
            self.fail(f"delete_api_key() raised unexpectedly: {exc}")

    # ------------------------------------------------------------------
    # 6. delete_api_key removes an existing key
    # ------------------------------------------------------------------

    def test_delete_api_key_removes_stored_key(self) -> None:
        """After delete_api_key(), get_api_key() must return ''."""
        credential_store.save_api_key("to-delete")
        credential_store.delete_api_key()
        self.assertEqual(credential_store.get_api_key(), "")


class TestApiKeyFile(unittest.TestCase):
    """DPAPI-encrypted key file (hard-set key, no settings dialog)."""

    def setUp(self) -> None:
        import tempfile

        _fake_keyring.reset()
        self._orig_app_dir = config.APP_DIR
        self._tmp = pathlib.Path(tempfile.mkdtemp())
        config.APP_DIR = self._tmp

    def tearDown(self) -> None:
        config.APP_DIR = self._orig_app_dir
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_set_then_get_roundtrip(self) -> None:
        """set_api_key_file writes an encrypted file that get_api_key decrypts."""
        credential_store.set_api_key_file("RGAPI-personal-key")
        # Stored file must NOT contain the plaintext key.
        raw = (self._tmp / "riot_api_key.dat").read_bytes()
        self.assertNotIn(b"RGAPI-personal-key", raw)
        self.assertEqual(credential_store.get_api_key(), "RGAPI-personal-key")

    def test_file_takes_precedence_over_wcm(self) -> None:
        """When both a file key and a WCM key exist, the file wins."""
        credential_store.save_api_key("wcm-key")          # legacy WCM entry
        credential_store.set_api_key_file("file-key")     # hard-set file
        self.assertEqual(credential_store.get_api_key(), "file-key")

    def test_get_falls_back_to_wcm_when_no_file(self) -> None:
        """With no file present, get_api_key reads the WCM entry (backward compat)."""
        credential_store.save_api_key("wcm-only")
        self.assertEqual(credential_store.get_api_key(), "wcm-only")

    def test_delete_file_falls_back_to_wcm(self) -> None:
        """After delete_api_key_file, get_api_key resolves the WCM entry again."""
        credential_store.save_api_key("wcm-fallback")
        credential_store.set_api_key_file("file-key")
        credential_store.delete_api_key_file()
        self.assertEqual(credential_store.get_api_key(), "wcm-fallback")

    def test_delete_file_when_absent_does_not_raise(self) -> None:
        credential_store.delete_api_key_file()  # must not raise


# ---------------------------------------------------------------------------
# Fake pywebview window — minimal .state stub (mirrors test_account_mgmt.py's
# _FakeTkRoot/_FakeWindowState, trimmed to what Controller._push_state needs).
# ---------------------------------------------------------------------------

class _FakeWindowState:
    """Attribute sink for window.state.* writes — accepts any attribute."""


class _FakeWindow:
    def __init__(self) -> None:
        self.state = _FakeWindowState()


# ---------------------------------------------------------------------------
# Plan 08-04 Task 1 — Controller: live key validation on save (D-03),
# immediate refresh (D-08), 401/403 header hint (D-09), get_settings/
# delete_api_key/set_gpu.
# ---------------------------------------------------------------------------

class TestControllerApiKeySettings(unittest.TestCase):
    """Unit tests for controller.py's Phase-8 Settings/onboarding surface."""

    def _make_controller_ctx(self, tmp_path: pathlib.Path):
        fake_window = _FakeWindow()
        patcher_app = patch.object(config, "APP_DIR", tmp_path)
        patcher_json = patch.object(config, "ACCOUNTS_JSON", tmp_path / "accounts.json")
        patcher_sessions = patch.object(config, "SESSIONS_DIR", tmp_path / "sessions")
        patcher_app.start()
        patcher_json.start()
        patcher_sessions.start()
        config.ensure_dirs()
        # Cross-file test-isolation guard (same pattern documented in
        # 08-03-SUMMARY.md): credential_store's module-level `keyring` binding
        # can be rebound by a sibling test file's importlib.reload() at
        # collection/run time, so `_fake_keyring.reset()` in setUp() (which
        # only clears THIS file's own fake-keyring instance) is not sufficient
        # to guarantee a clean slate. Clear via the real credential_store API
        # (writes through to whichever backend is currently bound) instead.
        credential_store.delete_api_key()
        credential_store.delete_api_key_file()
        ctrl = controller_module.Controller(fake_window)
        ctrl._fake_window = fake_window
        self._ctrls.append(ctrl)
        return ctrl, [patcher_app, patcher_json, patcher_sessions]

    def setUp(self) -> None:
        _fake_keyring.reset()
        self._ctrls = []

    def tearDown(self) -> None:
        for ctrl in self._ctrls:
            ctrl.shutdown()

    def test_save_api_key_invalid_does_not_store_or_refresh(self) -> None:
        """A key that fails live validation raises ValueError, is never stored,
        and never triggers a rank refresh (D-03)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, patchers = self._make_controller_ctx(pathlib.Path(tmp))
            try:
                with patch("rank_service.validate_api_key", return_value=False):
                    with patch.object(ctrl, "_trigger_rank_refresh") as mock_refresh:
                        with self.assertRaises(ValueError):
                            ctrl.save_api_key("RGAPI-bad-key")
                        mock_refresh.assert_not_called()
                self.assertFalse(ctrl.has_api_key())
                self.assertEqual(credential_store.get_api_key(), "")
            finally:
                for p in patchers:
                    p.stop()

    def test_save_api_key_valid_stores_then_refreshes_in_order(self) -> None:
        """A key that passes live validation is stored via credential_store,
        THEN triggers an immediate rank refresh (D-03/D-08, order asserted)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, patchers = self._make_controller_ctx(pathlib.Path(tmp))
            try:
                call_order = []
                orig_save = credential_store.save_api_key

                def _spy_save(key: str) -> None:
                    call_order.append("save")
                    orig_save(key)

                def _spy_refresh() -> None:
                    call_order.append("refresh")

                with patch("rank_service.validate_api_key", return_value=True):
                    with patch.object(credential_store, "save_api_key", side_effect=_spy_save):
                        with patch.object(ctrl, "_trigger_rank_refresh", side_effect=_spy_refresh):
                            ctrl.save_api_key("RGAPI-good-key")

                self.assertEqual(call_order, ["save", "refresh"])
                self.assertEqual(credential_store.get_api_key(), "RGAPI-good-key")
            finally:
                for p in patchers:
                    p.stop()

    def test_save_api_key_never_leaks_key_value(self) -> None:
        """Neither the invalid-key ValueError message nor the status message
        after a successful save ever contains the raw key string (T-02-05)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, patchers = self._make_controller_ctx(pathlib.Path(tmp))
            try:
                secret = "RGAPI-super-secret-value-12345"
                with patch("rank_service.validate_api_key", return_value=False):
                    try:
                        ctrl.save_api_key(secret)
                    except ValueError as exc:
                        self.assertNotIn(secret, str(exc))
                    else:
                        self.fail("expected ValueError")

                with patch("rank_service.validate_api_key", return_value=True):
                    with patch.object(ctrl, "_trigger_rank_refresh"):
                        ctrl.save_api_key(secret)
                self.assertNotIn(secret, ctrl.state.status_message)
            finally:
                for p in patchers:
                    p.stop()

    def test_save_api_key_network_error_raises_value_error(self) -> None:
        """A RequestException during validation surfaces as a ValueError, not
        an unhandled exception, and does not store the key."""
        import tempfile
        import requests.exceptions
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, patchers = self._make_controller_ctx(pathlib.Path(tmp))
            try:
                with patch(
                    "rank_service.validate_api_key",
                    side_effect=requests.exceptions.ConnectionError("boom"),
                ):
                    with self.assertRaises(ValueError):
                        ctrl.save_api_key("RGAPI-key")
                self.assertFalse(ctrl.has_api_key())
            finally:
                for p in patchers:
                    p.stop()

    def test_save_api_key_5xx_raises_value_error(self) -> None:
        """A RiotAPIError (e.g. 500/429) during validation surfaces as a
        ValueError rather than propagating the raw RiotAPIError."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, patchers = self._make_controller_ctx(pathlib.Path(tmp))
            try:
                with patch(
                    "rank_service.validate_api_key",
                    side_effect=rank_service.RiotAPIError(500, "API-Fehler 500"),
                ):
                    with self.assertRaises(ValueError):
                        ctrl.save_api_key("RGAPI-key")
                self.assertFalse(ctrl.has_api_key())
            finally:
                for p in patchers:
                    p.stop()

    def test_rank_error_401_sets_warning_then_ready_clears_it(self) -> None:
        """A 401 RiotAPIError during a rank fetch sets api_key_warning=True
        (pushed to window.state); a subsequent successful fetch resets it (D-09)."""
        import tempfile
        from models import RankInfo
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, patchers = self._make_controller_ctx(pathlib.Path(tmp))
            try:
                ctrl._on_rank_error(
                    "someuser", rank_service.RiotAPIError(401, "unauthorized")
                )
                self.assertTrue(ctrl._api_key_warning)
                self.assertTrue(ctrl._fake_window.state.api_key_warning)

                ctrl._on_rank_ready(
                    "someuser",
                    RankInfo(solo=None, flex=None, fetched_at=0.0, stale=False),
                )
                self.assertFalse(ctrl._api_key_warning)
                self.assertFalse(ctrl._fake_window.state.api_key_warning)
            finally:
                for p in patchers:
                    p.stop()

    def test_get_settings_shape_never_includes_raw_key(self) -> None:
        """get_settings() returns the documented keys; api_key_masked is the
        fixed 8-bullet mask (or '') and never the raw key value."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, patchers = self._make_controller_ctx(pathlib.Path(tmp))
            try:
                settings = ctrl.get_settings()
                self.assertEqual(
                    set(settings.keys()),
                    {
                        "has_api_key",
                        "api_key_masked",
                        "language",
                        "update_check_enabled",
                        "disable_gpu",
                    },
                )
                self.assertFalse(settings["has_api_key"])
                self.assertEqual(settings["api_key_masked"], "")

                credential_store.save_api_key("RGAPI-some-key")
                settings2 = ctrl.get_settings()
                self.assertTrue(settings2["has_api_key"])
                self.assertEqual(settings2["api_key_masked"], "••••••••")
                self.assertNotIn("RGAPI-some-key", str(settings2))
            finally:
                for p in patchers:
                    p.stop()

    def test_set_gpu_persists_disable_gpu_inverted(self) -> None:
        """set_gpu(True) -> disable_gpu=False; set_gpu(False) -> disable_gpu=True
        (round-tripped via config.save_state/load_state)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, patchers = self._make_controller_ctx(pathlib.Path(tmp))
            try:
                ctrl.set_gpu(True)
                self.assertFalse(ctrl.state.disable_gpu)
                reloaded = config.load_state()
                self.assertFalse(reloaded.disable_gpu)

                ctrl.set_gpu(False)
                self.assertTrue(ctrl.state.disable_gpu)
                reloaded2 = config.load_state()
                self.assertTrue(reloaded2.disable_gpu)
            finally:
                for p in patchers:
                    p.stop()

    def test_delete_api_key_removes_key_and_clears_warning(self) -> None:
        """delete_api_key() removes both WCM + DPAPI-file entries and clears
        the persistent expiry hint."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, patchers = self._make_controller_ctx(pathlib.Path(tmp))
            try:
                credential_store.save_api_key("RGAPI-to-delete")
                ctrl._api_key_warning = True
                ctrl.delete_api_key()
                self.assertFalse(ctrl.has_api_key())
                self.assertFalse(ctrl._api_key_warning)
                self.assertFalse(ctrl._fake_window.state.api_key_warning)
            finally:
                for p in patchers:
                    p.stop()

    def test_set_update_check_persists(self) -> None:
        """set_update_check(False) -> update_check_enabled=False persisted
        (config round-trip); set_update_check(True) -> back to True (Plan 08-06,
        D-07/D-14)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, patchers = self._make_controller_ctx(pathlib.Path(tmp))
            try:
                ctrl.set_update_check(False)
                self.assertFalse(ctrl.state.update_check_enabled)
                reloaded = config.load_state()
                self.assertFalse(reloaded.update_check_enabled)

                ctrl.set_update_check(True)
                self.assertTrue(ctrl.state.update_check_enabled)
                reloaded2 = config.load_state()
                self.assertTrue(reloaded2.update_check_enabled)
            finally:
                for p in patchers:
                    p.stop()


# ---------------------------------------------------------------------------
# Plan 08-06 Task 2 — content-grep gates for the update-pill markup/wiring.
#
# Regression guard: catches an accidental removal of the pill markup or its
# JS wiring that unit tests alone (no real browser/webview in this suite)
# cannot otherwise exercise. Mirrors test_i18n.py's TestJsWiringContentGates.
# ---------------------------------------------------------------------------

class TestUpdatePillContentGates(unittest.TestCase):
    """Content-grep gates for the header update pill (ONBOARD-03, D-13/D-14)."""

    _REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

    def _app_js_text(self) -> str:
        return (self._REPO_ROOT / "gui" / "assets" / "app.js").read_text(encoding="utf-8")

    def _index_html_text(self) -> str:
        return (self._REPO_ROOT / "gui" / "assets" / "index.html").read_text(encoding="utf-8")

    def test_index_html_has_update_pill_once(self) -> None:
        self.assertEqual(self._index_html_text().count('id="update-pill"'), 1)

    def test_index_html_update_pill_reuses_mint_token(self) -> None:
        """No new color token invented — reuses the existing .client-status.running
        mint rgba(34,224,192...) variant (STYLE-REFERENCE)."""
        self.assertGreaterEqual(self._index_html_text().count("rgba(34,224,192"), 1)

    def test_app_js_has_render_update_pill(self) -> None:
        self.assertIn("renderUpdatePill", self._app_js_text())

    def test_app_js_calls_render_update_pill_from_render(self) -> None:
        self.assertIn("renderUpdatePill(state)", self._app_js_text())

    def test_app_js_wires_open_external_url_for_pill(self) -> None:
        self.assertIn("open_external_url", self._app_js_text())

    def test_app_js_wires_dismiss_update(self) -> None:
        self.assertIn("dismiss_update", self._app_js_text())

    def test_app_js_wires_set_update_check(self) -> None:
        self.assertIn("set_update_check", self._app_js_text())


if __name__ == "__main__":
    unittest.main()
