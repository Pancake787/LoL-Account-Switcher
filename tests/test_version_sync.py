"""tests/test_version_sync.py — WR-01 regression guard.

Fails the build if ``version.APP_VERSION`` (the single in-code source of
truth, imported by ``update_checker.py``) ever diverges from the
installer's ``AppVersion=`` line in ``installer/LoLSwitcher.iss``. This is
the enforcement mechanism for the invariant documented in ``version.py`` —
without it, a maintainer bumping one of the two values and forgetting the
other would silently break the update-check comparison again, exactly as
already happened once before this fix (2.1.0 vs 2.0.0 drift found in
Phase 8 review).
"""
from __future__ import annotations

import pathlib
import re
import unittest

import update_checker
import version

_ISS_PATH = pathlib.Path(__file__).resolve().parent.parent / "installer" / "LoLSwitcher.iss"
_APP_VERSION_RE = re.compile(r"^AppVersion=(.+)$", re.MULTILINE)


def _read_iss_app_version() -> str:
    """Parse the ``AppVersion=`` line out of installer/LoLSwitcher.iss."""
    text = _ISS_PATH.read_text(encoding="utf-8")
    match = _APP_VERSION_RE.search(text)
    if not match:
        raise AssertionError(
            f"Could not find an 'AppVersion=' line in {_ISS_PATH}"
        )
    return match.group(1).strip()


class TestVersionSync(unittest.TestCase):
    def test_installer_app_version_matches_version_module(self) -> None:
        iss_version = _read_iss_app_version()
        self.assertEqual(
            iss_version,
            version.APP_VERSION,
            "installer/LoLSwitcher.iss AppVersion "
            f"({iss_version!r}) has drifted from version.APP_VERSION "
            f"({version.APP_VERSION!r}). Bump both together in the same "
            "commit at the next release cut (see version.py docstring).",
        )

    def test_update_checker_reexports_the_same_version_object(self) -> None:
        """update_checker.APP_VERSION must be the SAME value as
        version.APP_VERSION — no local duplicate/hardcoded copy."""
        self.assertEqual(update_checker.APP_VERSION, version.APP_VERSION)


if __name__ == "__main__":
    unittest.main()
