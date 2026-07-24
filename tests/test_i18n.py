"""Tests for gui/i18n.py (ONBOARD-04).

Hermetic: strings_path() is monkeypatched to a temp catalog wherever the
test needs a controlled/corrupt catalog. GetUserDefaultUILanguage() is
monkeypatched for detect_default_language() tests — never depends on the
real Windows display language.
"""
from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from gui import i18n


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


if __name__ == "__main__":
    unittest.main()
