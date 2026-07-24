"""version.py — single source of truth for the app's own version string.

WR-01 fix: previously `update_checker.APP_VERSION` was a hand-maintained
duplicate of the installer's `AppVersion` (installer/LoLSwitcher.iss) — the
two had already drifted (2.1.0 vs 2.0.0) before Phase 8 even shipped, and
nothing enforced they stay in sync. `check_for_update` compares this value
against the newest GitHub release tag, so any future drift would silently
make the update check wrong (either missing a real update or falsely
claiming "up to date").

This module holds the ONE in-code constant; `update_checker.py` imports it
from here instead of hardcoding its own copy. `tests/test_version_sync.py`
parses `installer/LoLSwitcher.iss`'s `AppVersion=` line and fails the build
if it ever diverges from `APP_VERSION` below — that test (not this comment)
is what actually enforces the invariant.

Value choice: kept at "2.0.0" to match the CURRENTLY RELEASED/installed
baseline (the last real installer build, v2.0.0) rather than pre-bumping to
an unreleased "2.1.0". Phase 8's work is not yet released; bumping this
value ahead of the installer would make `check_for_update` wrongly treat the
actual, already-shipped v2.0.0 GitHub release as "not an update" while ALSO
comparing against a version nothing has actually installed. Bump this
constant AND installer/LoLSwitcher.iss's `AppVersion=` together, in the same
commit, at the next release cut — the sync test will fail loudly if only
one of the two is updated.

Pure stdlib, no dependencies — safe to import from any module boundary
(GUI, CLI/headless, or update_checker.py's own requests-only boundary).
"""
from __future__ import annotations

#: Canonical current app version. Keep in lockstep with installer's
#: `AppVersion=` (installer/LoLSwitcher.iss) — enforced by
#: tests/test_version_sync.py.
APP_VERSION: str = "2.0.0"
