"""Regression guard for the taskbar-icon fix (D-15).

Lightweight, no webview launch, no visual assertions (visual proof is the
human smoke-test in 06-04). Verifies:

1. ``_set_app_user_model_id()`` calls
   ``ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID`` exactly
   once with the stable id ``"LoLAccountSwitcher.Desktop"`` and never raises
   (defensive try/except).
2. ``_icon_path()`` resolves to a path ending in ``assets/icon.ico`` (or the
   Windows-separator equivalent) and, in dev mode, points at a file that
   actually exists on disk.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

import gui.webview_window as webview_window


class TestSetAppUserModelId(unittest.TestCase):
    """_set_app_user_model_id() — AUMID helper (D-15)."""

    def test_invokes_set_current_process_explicit_app_user_model_id_once(self):
        """Calls SetCurrentProcessExplicitAppUserModelID once with the stable id."""
        with mock.patch(
            "ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID"
        ) as mock_set_aumid:
            webview_window._set_app_user_model_id()

        mock_set_aumid.assert_called_once_with("LoLAccountSwitcher.Desktop")

    def test_never_raises_even_if_shell32_call_fails(self):
        """Stays defensive — must never raise, even if the ctypes call errors."""
        with mock.patch(
            "ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID",
            side_effect=OSError("simulated failure"),
        ):
            try:
                webview_window._set_app_user_model_id()
            except Exception as exc:  # noqa: BLE001 — explicitly asserting no raise
                self.fail(f"_set_app_user_model_id() raised unexpectedly: {exc!r}")


class TestIconPath(unittest.TestCase):
    """_icon_path() — bundled window-icon resolver (D-15)."""

    def test_ends_with_assets_icon_ico(self):
        """Path resolves to the assets/icon.ico segments (dev mode, non-frozen)."""
        path = webview_window._icon_path()
        normalized = path.replace("\\", "/")
        self.assertTrue(
            normalized.endswith("assets/icon.ico"),
            f"Expected path to end with assets/icon.ico, got: {path!r}",
        )

    def test_dev_mode_path_points_at_existing_file(self):
        """In dev mode (not frozen), the resolved path must exist on disk."""
        path = webview_window._icon_path()
        self.assertTrue(
            os.path.exists(path),
            f"_icon_path() resolved to a non-existent file in dev mode: {path!r}",
        )


if __name__ == "__main__":
    unittest.main()
