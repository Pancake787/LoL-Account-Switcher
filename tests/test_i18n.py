"""Tests for gui/i18n.py (ONBOARD-04).

Hermetic: strings_path() is monkeypatched to a temp catalog wherever the
test needs a controlled/corrupt catalog. GetUserDefaultUILanguage() is
monkeypatched for detect_default_language() tests — never depends on the
real Windows display language.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

from gui import i18n

# ---------------------------------------------------------------------------
# Fake keyring module — backed by a simple dict, never touches WCM.
# Mirrors the _FakeKeyring pattern in test_account_mgmt.py/test_settings.py
# exactly, so importing controller.py (Plan 08-05 bridge-contract tests below)
# stays hermetic even when this file is the FIRST to import credential_store
# in a given pytest run (e.g. `pytest tests/test_i18n.py tests/test_js_api.py`
# in isolation, per the plan's own verify command).
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

# Inject fake keyring into sys.modules BEFORE importing credential_store —
# no-op if a sibling test file already imported credential_store first (its
# module-level `keyring` binding is then fixed for the whole process); the
# per-test `credential_store.delete_api_key()` cleanup below handles that
# case regardless of which backend ended up bound (08-04-SUMMARY precedent).
if "keyring" not in sys.modules:
    _keyring_mod = types.ModuleType("keyring")
    _keyring_mod.set_password = _fake_keyring.set_password
    _keyring_mod.get_password = _fake_keyring.get_password
    _keyring_mod.delete_password = _fake_keyring.delete_password
    _keyring_errors_mod = types.ModuleType("keyring.errors")
    _keyring_errors_mod.PasswordDeleteError = _FakeKeyringErrors.PasswordDeleteError
    _keyring_mod.errors = _keyring_errors_mod
    sys.modules["keyring"] = _keyring_mod
    sys.modules["keyring.errors"] = _keyring_errors_mod

import config  # noqa: E402
import controller as controller_module  # noqa: E402
import credential_store  # noqa: E402
from models import Account  # noqa: E402


class TestRealCatalogLoadsCleanly(unittest.TestCase):
    """Sanity: the real, shipped strings.json loads without error."""

    def setUp(self):
        i18n.reload()
        i18n.set_language("en")

    def test_real_catalog_has_de_and_en(self):
        self.assertEqual(set(i18n._STRINGS), {"de", "en"})

    def test_de_en_key_sets_match(self):
        self.assertEqual(set(i18n._STRINGS["de"]), set(i18n._STRINGS["en"]))

    def test_pitfall3_spot_check_keys_present_both_languages(self):
        """RESEARCH.md Pitfall 3 spot-check: these four keys must exist in both langs."""
        spot_check = [
            "status.killing_client",
            "error.account_exists",
            "status.done_active",
            "riotapi.rate_limit",
        ]
        for key in spot_check:
            self.assertIn(key, i18n._STRINGS["de"], f"{key} missing from de")
            self.assertIn(key, i18n._STRINGS["en"], f"{key} missing from en")


class TestInterpolation(unittest.TestCase):
    def setUp(self):
        i18n.reload()

    def test_t_resolves_and_interpolates_german(self):
        i18n.set_language("de")
        result = i18n.t("status.done_active", name="Main")
        self.assertEqual(result, "Fertig — Main ist aktiv.")

    def test_t_resolves_and_interpolates_english(self):
        i18n.set_language("en")
        result = i18n.t("status.done_active", name="Main")
        self.assertEqual(result, "Done — Main is now active.")

    def test_t_unknown_key_returns_raw_key(self):
        i18n.set_language("en")
        result = i18n.t("nonexistent.key")
        self.assertEqual(result, "nonexistent.key")

    def test_t_missing_param_returns_unformatted_template_not_raise(self):
        i18n.set_language("en")
        # status.done_active expects {name} — omit it, must not raise.
        result = i18n.t("status.done_active")
        self.assertEqual(result, "Done — {name} is now active.")


class TestDegradedCatalog(unittest.TestCase):
    """Corrupt/missing catalog must degrade to raw-key fallback, never raise."""

    def test_missing_catalog_file_degrades_to_raw_key(self):
        missing_path = pathlib.Path(tempfile.gettempdir()) / "does-not-exist-i18n.json"
        with patch("gui.i18n.strings_path", return_value=missing_path):
            i18n.reload()
            result = i18n.t("status.killing_client")
        self.assertEqual(result, "status.killing_client")
        i18n.reload()  # restore real catalog for subsequent tests

    def test_corrupt_catalog_file_degrades_to_raw_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            corrupt = pathlib.Path(tmp) / "strings.json"
            corrupt.write_text("{not valid json", encoding="utf-8")
            with patch("gui.i18n.strings_path", return_value=corrupt):
                i18n.reload()
                result = i18n.t("status.killing_client")
            self.assertEqual(result, "status.killing_client")
        i18n.reload()  # restore real catalog for subsequent tests

    def test_custom_minimal_catalog_resolves_known_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            custom = pathlib.Path(tmp) / "strings.json"
            custom.write_text(
                json.dumps({"de": {"greeting": "Hallo {name}"}, "en": {"greeting": "Hello {name}"}}),
                encoding="utf-8",
            )
            with patch("gui.i18n.strings_path", return_value=custom):
                i18n.reload()
                i18n.set_language("de")
                result = i18n.t("greeting", name="Maik")
            self.assertEqual(result, "Hallo Maik")
        i18n.reload()  # restore real catalog for subsequent tests


class TestGetSetLanguage(unittest.TestCase):
    def test_default_language_is_en(self):
        # Reset module state to its documented default for this assertion.
        i18n.set_language("en")
        self.assertEqual(i18n.get_language(), "en")

    def test_set_language_roundtrip(self):
        i18n.set_language("de")
        self.assertEqual(i18n.get_language(), "de")
        i18n.set_language("en")


class TestDetectDefaultLanguage(unittest.TestCase):
    def test_german_windows_returns_de(self):
        mock_kernel32 = MagicMock()
        mock_kernel32.GetUserDefaultUILanguage.return_value = 0x0407  # de-DE
        with patch("gui.i18n.ctypes.windll.kernel32", mock_kernel32):
            self.assertEqual(i18n.detect_default_language(), "de")

    def test_german_austria_variant_also_returns_de(self):
        mock_kernel32 = MagicMock()
        mock_kernel32.GetUserDefaultUILanguage.return_value = 0x0C07  # de-AT
        with patch("gui.i18n.ctypes.windll.kernel32", mock_kernel32):
            self.assertEqual(i18n.detect_default_language(), "de")

    def test_english_windows_returns_en(self):
        mock_kernel32 = MagicMock()
        mock_kernel32.GetUserDefaultUILanguage.return_value = 0x0409  # en-US
        with patch("gui.i18n.ctypes.windll.kernel32", mock_kernel32):
            self.assertEqual(i18n.detect_default_language(), "en")

    def test_exception_returns_en(self):
        mock_kernel32 = MagicMock()
        mock_kernel32.GetUserDefaultUILanguage.side_effect = OSError("boom")
        with patch("gui.i18n.ctypes.windll.kernel32", mock_kernel32):
            self.assertEqual(i18n.detect_default_language(), "en")


class _FakeWindowState:
    """Attribute sink for window.state.* writes — accepts any attribute."""


class _FakeWindow:
    def __init__(self) -> None:
        self.state = _FakeWindowState()


class TestControllerI18nBridgeContract(unittest.TestCase):
    """Plan 08-05 (ONBOARD-04) controller-side bridge-contract assertions.

    Covers the acceptance criteria: status_key/status_params contract,
    translated ValueErrors, set_language persistence + live push, and the
    first-run System-Locale default.
    """

    def _make_controller(self, tmp_path: pathlib.Path, language=None):
        """Construct a hermetic Controller against a temp accounts.json.

        If *language* is given, it is pre-written into accounts.json so
        ``config.load_state()`` returns it directly (detection skipped,
        matching a returning user with a persisted choice). If *language*
        is None, no file is written — Controller.__init__ must run the
        first-run System-Locale detection (D-15).
        """
        accounts_json = tmp_path / "accounts.json"
        if language is not None:
            accounts_json.write_text(
                json.dumps({"accounts": [], "active_username": None, "language": language}),
                encoding="utf-8",
            )
        patcher_app = patch.object(config, "APP_DIR", tmp_path)
        patcher_json = patch.object(config, "ACCOUNTS_JSON", accounts_json)
        patcher_sessions = patch.object(config, "SESSIONS_DIR", tmp_path / "sessions")
        patcher_app.start()
        patcher_json.start()
        patcher_sessions.start()
        self.addCleanup(patcher_app.stop)
        self.addCleanup(patcher_json.stop)
        self.addCleanup(patcher_sessions.stop)
        config.ensure_dirs()
        # Cross-file fake-keyring test-isolation quirk (08-04-SUMMARY precedent):
        # clear via the real credential_store API (writes through to whichever
        # backend ended up bound at first import), guaranteeing a clean slate
        # regardless of test-file collection order.
        credential_store.delete_api_key()
        credential_store.delete_api_key_file()

        fake_window = _FakeWindow()
        ctrl = controller_module.Controller(fake_window)
        self.addCleanup(ctrl.shutdown)
        return ctrl, fake_window

    def tearDown(self) -> None:
        # Module-global i18n state must not leak into other test files.
        i18n.set_language("en")

    def test_switch_step_pushes_killing_client_status_key(self) -> None:
        """switch_account's first status update sets window.state.status_key
        to the RAW key "status.killing_client" (not pre-formatted German
        text) with empty params — the core of the bridge-contract change."""
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, fake_window = self._make_controller(pathlib.Path(tmp), language="de")
            acc = Account(username="u1", display_name="Main", has_snapshot=True)
            ctrl.state.accounts = [acc]
            # Patch out the background thread so the assertion below reads
            # state exactly as switch_account() left it on the main thread —
            # no race with the worker thread's own subsequent status updates.
            with patch("controller.threading.Thread") as mock_thread, \
                 patch("riot_client.is_game_running", return_value=False):
                ctrl.switch_account(acc)
            mock_thread.assert_called_once()
            self.assertEqual(fake_window.state.status_key, "status.killing_client")
            self.assertEqual(fake_window.state.status_params, {})

    def test_rank_401_sets_api_key_invalid_status_key(self) -> None:
        """A 401 RiotAPIError from a background rank fetch sets
        window.state.status_key == "status.api_key_invalid" (D-09/ONBOARD-04)."""
        from rank_service import RiotAPIError
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, fake_window = self._make_controller(pathlib.Path(tmp), language="de")
            acc = Account(username="u1", display_name="Main", has_snapshot=True, puuid="puuid-1")
            ctrl.state.accounts = [acc]
            ctrl._on_rank_error("u1", RiotAPIError(401, "unauthorized"))
            self.assertEqual(fake_window.state.status_key, "status.api_key_invalid")
            self.assertEqual(fake_window.state.status_params, {})
            self.assertTrue(fake_window.state.api_key_warning)

    def test_duplicate_account_error_is_translated_and_matches_de_literal(self) -> None:
        """add_account's duplicate-username ValueError equals gui.i18n.t(...)
        for the current language, and the DE text is byte-identical to the
        pre-refactor literal (D-15: zero visible change for existing DE users)."""
        with tempfile.TemporaryDirectory() as tmp:
            ctrl, _ = self._make_controller(pathlib.Path(tmp), language="de")
            ctrl.add_account("Main", "dupuser", "pw1")
            with self.assertRaises(ValueError) as cm:
                ctrl.add_account("Second", "dupuser", "pw2")
            expected = i18n.t("error.account_exists", username="dupuser")
            self.assertEqual(str(cm.exception), expected)
            self.assertEqual(
                str(cm.exception),
                "Ein Account mit dem Benutzernamen „dupuser“ ist bereits vorhanden.",
            )

    def test_set_language_switches_persists_and_pushes(self) -> None:
        """set_language("en") flips gui.i18n's current language, persists
        state.language via config.save_state, and pushes window.state.language."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            ctrl, fake_window = self._make_controller(tmp_path, language="de")
            ctrl.set_language("en")
            self.assertEqual(i18n.get_language(), "en")
            self.assertEqual(ctrl.state.language, "en")
            self.assertEqual(fake_window.state.language, "en")
            # Config round-trip — persisted, not just in-memory.
            reloaded = config.load_state()
            self.assertEqual(reloaded.language, "en")

    def test_first_run_locale_default_applied(self) -> None:
        """A fresh controller (state.language is None) sets state.language
        from gui.i18n.detect_default_language() (D-15)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            with patch.object(i18n, "detect_default_language", return_value="de"):
                ctrl, _ = self._make_controller(tmp_path, language=None)
            self.assertEqual(ctrl.state.language, "de")
            self.assertEqual(i18n.get_language(), "de")


class TestJsWiringContentGates(unittest.TestCase):
    """Plan 08-05 Task 2 — content-grep gates for the app.js/index.html wiring.

    Regression guard: catches an accidental removal of the JS i18n plumbing
    (the fetch/resolver/live-re-render/select wiring) that unit tests alone
    (no real browser/webview in this suite) cannot otherwise exercise.
    """

    _REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

    def _app_js_text(self) -> str:
        return (self._REPO_ROOT / "gui" / "assets" / "app.js").read_text(encoding="utf-8")

    def _index_html_text(self) -> str:
        return (self._REPO_ROOT / "gui" / "assets" / "index.html").read_text(encoding="utf-8")

    def test_app_js_fetches_strings_json(self) -> None:
        self.assertIn("i18n/strings.json", self._app_js_text())

    def test_app_js_has_apply_language(self) -> None:
        self.assertIn("applyLanguage", self._app_js_text())

    def test_app_js_resolves_status_key(self) -> None:
        self.assertIn("status_key", self._app_js_text())

    def test_app_js_wires_set_language(self) -> None:
        self.assertIn("set_language", self._app_js_text())

    def test_index_html_has_at_least_8_data_i18n_labels(self) -> None:
        self.assertGreaterEqual(self._index_html_text().count("data-i18n"), 8)


class TestWr02LiveRetranslationCoverage(unittest.TestCase):
    """08-REVIEW.md WR-02 regression guard: the specific hardcoded German
    literals the review found in renderClientStatus/renderEmptyState/
    renderSubline and the modal error/success/toast call sites must never
    reappear as raw string literals passed to innerHTML/textContent/
    showModalError/showModalSuccess/showToast — they must be routed through
    t()/catalog keys instead."""

    _REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

    def _app_js_text(self) -> str:
        return (self._REPO_ROOT / "gui" / "assets" / "app.js").read_text(encoding="utf-8")

    #: The exact hardcoded-literal call sites the review cited (WR-02).
    _FORBIDDEN_LITERALS = [
        "'Wechsel läuft…'",
        "'Im Match'",
        "'Client läuft'",
        "'Offline'",
        "'Keine Accounts'",
        "Noch keine Accounts",
        "Füg deinen ersten Account",
        "'Bitte Anzeigename, Benutzername und Passwort ausfüllen.'",
        "'Bitte einen neuen Anzeigenamen eingeben.'",
        "'Bitte einen API-Key eingeben.'",
        "'Unbekannter Fehler'",
        "'API-Key gespeichert.'",
        "'API-Key gelöscht.'",
        "'Passwort kopiert — wird in 30s gelöscht'",
        "'Kein Passwort gespeichert'",
        "'Fehler beim Kopieren'",
    ]

    def test_no_hardcoded_german_literals_remain_in_rerender_paths(self) -> None:
        text = self._app_js_text()
        offenders = [lit for lit in self._FORBIDDEN_LITERALS if lit in text]
        self.assertEqual(
            offenders,
            [],
            f"Hardcoded German literal(s) still present in app.js (WR-02): {offenders}",
        )

    #: Every new catalog key introduced to close WR-02 — must resolve in
    #: BOTH languages (mirrors TestControllerEmittedKeyParity below).
    _WR02_NEW_KEYS = [
        "error.fill_required", "error.new_name_required", "error.api_key_required",
        "error.unknown", "settings.api_key_deleted",
        "toast.password_copied", "toast.no_password_stored", "toast.copy_error",
        "ui.no_accounts", "ui.active", "ui.empty_title", "ui.empty_hint",
    ]

    def test_wr02_new_keys_present_in_both_languages(self) -> None:
        i18n.reload()
        for key in self._WR02_NEW_KEYS:
            self.assertIn(key, i18n._STRINGS.get("de", {}), f"{key} missing from de catalog")
            self.assertIn(key, i18n._STRINGS.get("en", {}), f"{key} missing from en catalog")

    def test_wr02_new_keys_referenced_from_app_js(self) -> None:
        """Guards against a key existing in the catalog but never actually
        wired to a call site (the exact gap IN-04 warns weak tests miss)."""
        text = self._app_js_text()
        for key in self._WR02_NEW_KEYS:
            self.assertIn(f"t('{key}')", text, f"{key} is not referenced via t(...) in app.js")

    def test_apply_language_translates_placeholder_attributes(self) -> None:
        """WR-03: applyLanguage() must also resolve [data-i18n-placeholder]
        elements' placeholder attribute, not just [data-i18n] textContent."""
        text = self._app_js_text()
        self.assertIn("data-i18n-placeholder", text)
        self.assertIn(".placeholder = t(", text)


class TestWr03WelcomeDialogTranslationCoverage(unittest.TestCase):
    """08-REVIEW.md WR-03 regression guard: the welcome dialog's actual
    instructional copy (intro paragraph + steps 2/3 + key input placeholder)
    must be wired via data-i18n/data-i18n-placeholder, not hardcoded German —
    otherwise an English-locale first-run user sees a partially-German
    onboarding dialog, defeating ONBOARD-01's stranger-onboarding goal."""

    _REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

    def _index_html_text(self) -> str:
        return (self._REPO_ROOT / "gui" / "assets" / "index.html").read_text(encoding="utf-8")

    def test_welcome_intro_has_data_i18n(self) -> None:
        self.assertIn('data-i18n="onboard.intro"', self._index_html_text())

    def test_welcome_step2_and_step3_are_wired(self) -> None:
        text = self._index_html_text()
        self.assertIn('data-i18n="onboard.step2"', text)
        self.assertIn('data-i18n="onboard.step3"', text)

    def test_welcome_api_key_placeholder_uses_catalog_key(self) -> None:
        self.assertIn(
            'data-i18n-placeholder="onboard.key_placeholder"',
            self._index_html_text(),
        )

    def test_onboard_intro_key_present_in_both_languages(self) -> None:
        i18n.reload()
        self.assertIn("onboard.intro", i18n._STRINGS.get("de", {}))
        self.assertIn("onboard.intro", i18n._STRINGS.get("en", {}))


class TestControllerEmittedKeyParity(unittest.TestCase):
    """Key-drift parity (Plan 08-05 Task 2): every status.*/error.*/riotapi.*
    key the controller can emit must exist in BOTH `de` and `en` catalogs —
    guards against toggling to English still showing German leftovers
    (RESEARCH.md Pitfall 3 warning sign)."""

    #: Enumerated from every _set_status/_post_status/_post_error/i18n.t()/
    #: _translate_riot_error() call site wired in controller.py by this plan.
    _EMITTED_KEYS = [
        "status.blocked_match", "status.killing_client", "status.kill_failed",
        "status.no_snapshot", "status.riot_not_found_switched", "status.unknown_error",
        "status.switching_session", "status.riot_not_found_manual", "status.unknown_error_exc",
        "status.pending_login", "status.snapshot_saved", "status.no_login_yet",
        "status.recapture_blocked", "status.recapture_resetting", "status.recapture_not_found",
        "status.done_active", "status.api_key_saved", "status.api_key_invalid",
        "error.name_empty", "error.username_empty", "error.password_empty",
        "error.account_exists", "error.riot_id_slash", "error.region_invalid",
        "error.riot_id_format", "error.riot_id_not_found", "error.riot_id_network",
        "error.api_key_network", "error.api_key_invalid",
        "riotapi.key_unauthorized", "riotapi.player_not_found",
        "riotapi.rate_limit", "riotapi.api_error",
    ]

    def test_every_emitted_key_present_in_both_languages(self) -> None:
        i18n.reload()
        for key in self._EMITTED_KEYS:
            self.assertIn(key, i18n._STRINGS.get("de", {}), f"{key} missing from de catalog")
            self.assertIn(key, i18n._STRINGS.get("en", {}), f"{key} missing from en catalog")


if __name__ == "__main__":
    unittest.main()
