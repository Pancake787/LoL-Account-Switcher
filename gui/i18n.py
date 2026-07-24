"""gui/i18n.py — Python-side i18n loader for the shared DE/EN string catalog (ONBOARD-04).

Module boundary: imports only stdlib (ctypes, json, pathlib, sys). MUST NOT
import controller/webview — same gui/-package boundary already crossed by
gui/_clipboard.py (imported by controller.py in the GUI-only path).

Loads ``gui/assets/i18n/strings.json`` — the single shared source of truth
also read by the JS side via ``fetch()`` (Plan 08-05 wires the JS consumer).
Degrades safely: a missing or corrupt catalog never crashes the app — ``t()``
falls back to returning the raw key (config._try_load guarded-read precedent).
"""
from __future__ import annotations

import ctypes
import json
import pathlib
import sys

#: Win32 PRIMARYLANGID for German — stable across all German locale variants
#: (de-DE, de-AT, de-CH, de-LU, de-LI all share primary language id 0x07).
_LANG_GERMAN: int = 0x07

#: Module-level current-language state (default "en" per artifact spec).
_lang: str = "en"

#: In-memory catalog: {"de": {...}, "en": {...}}. Populated by reload()/_load_strings()
#: at import time; degrades to {} on any load failure.
_STRINGS: dict = {}


def strings_path() -> pathlib.Path:
    """Return the absolute path to ``gui/assets/i18n/strings.json``.

    Mirrors ``gui/webview_window.py``'s ``_asset_root()`` frozen/dev
    path-resolution pattern (``sys._MEIPASS`` vs dev-relative).

    Returns:
        Absolute Path to the strings.json catalog.
    """
    if getattr(sys, "frozen", False):
        # PyInstaller extracts package to sys._MEIPASS at runtime
        base = pathlib.Path(sys._MEIPASS) / "gui" / "assets"  # type: ignore[attr-defined]
    else:
        # Dev mode: this file lives at gui/i18n.py; assets/ is a sibling
        base = pathlib.Path(__file__).parent / "assets"
    return base / "i18n" / "strings.json"


def _load_strings() -> dict:
    """Load and parse the strings.json catalog.

    Guarded read mirroring config.py's ``_try_load`` shape: never raises.
    Returns an empty dict on any missing/corrupt/malformed catalog so ``t()``
    degrades to raw-key fallback rather than crashing the app.

    Returns:
        The parsed catalog dict, or {} on any failure.
    """
    path = strings_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — corrupt/missing catalog must never crash
        return {}


def reload() -> None:
    """(Re)load the string catalog from disk into the module-level cache."""
    global _STRINGS
    _STRINGS = _load_strings()


def get_language() -> str:
    """Return the current active language code ("de" or "en")."""
    return _lang


def set_language(lang: str) -> None:
    """Set the current active language code.

    Args:
        lang: Language code, e.g. "de" or "en". No validation is performed
            here — an unrecognized code simply resolves every key to its
            raw-key fallback via ``t()`` (never raises).
    """
    global _lang
    _lang = lang


def t(key: str, **params) -> str:
    """Resolve *key* in the current language catalog, with {param} interpolation.

    Args:
        key: Dotted catalog key, e.g. "status.done_active".
        **params: Values substituted into ``{param}`` placeholders in the
            resolved template via ``str.format``.

    Returns:
        The resolved, interpolated string. Falls back to the raw *key* when
        the catalog is missing the entry, the catalog failed to load, or the
        template does not match the params (never raises).
    """
    template = _STRINGS.get(_lang, {}).get(key, key)
    if not params:
        return template
    try:
        return template.format(**params)
    except (KeyError, IndexError, ValueError):
        # Missing/mismatched param — return the unformatted template rather
        # than raising, so a catalog/call-site drift never crashes the app.
        return template


def detect_default_language() -> str:
    """Detect the first-run default UI language from the Windows display language.

    Uses ``GetUserDefaultUILanguage()`` (ctypes, frozen-safe) rather than the
    deprecated ``locale.getdefaultlocale()`` — matches this project's
    ctypes-first, no-pywin32 discipline (credential_store._dpapi(),
    gui/_clipboard.py).

    Returns:
        "de" on any German-language Windows install (PRIMARYLANGID 0x07,
        covers de-DE/de-AT/de-CH/de-LU/de-LI), else "en". Best-effort: "en"
        on any exception (non-Windows, missing DLL, etc.).
    """
    try:
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
        primary = langid & 0x3FF
        return "de" if primary == _LANG_GERMAN else "en"
    except Exception:  # noqa: BLE001 — best-effort, matches _clipboard.py's import-guard style
        return "en"


# Load the catalog once at import time.
reload()
