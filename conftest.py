"""Global pytest fixtures.

Deterministic UI language across hosts: Phase 8 made the first-run default
language follow the Windows display language (gui.i18n.detect_default_language
via GetUserDefaultUILanguage). That makes any test which constructs a Controller
— and asserts a German status string — pass on a German dev machine but FAIL on
an English CI runner (windows-latest). This autouse fixture pins the detected
locale to German for every test so status-message assertions are host-independent.

The four dedicated detect-language tests in tests/test_i18n.py re-patch the same
ctypes target inside their own `with patch(...)` block, so the inner patch wins
for their duration and they keep exercising the real en/de/exception branches.
Tests that call i18n.set_language(...) explicitly override the pinned default.
"""

from unittest.mock import MagicMock, patch

import pytest

import gui.i18n as i18n

_LANGID_DE_DE = 0x0407  # PRIMARYLANGID 0x07 (German), deterministic across hosts


@pytest.fixture(autouse=True)
def _deterministic_ui_language():
    saved = i18n.get_language()
    mock_kernel32 = MagicMock()
    mock_kernel32.GetUserDefaultUILanguage.return_value = _LANGID_DE_DE
    with patch("gui.i18n.ctypes.windll.kernel32", mock_kernel32):
        i18n.set_language("de")
        yield
    i18n.set_language(saved)
