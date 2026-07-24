"""Windows clipboard helpers using ctypes only — no tkinter, no pyperclip.

Provides clipboard_set, clipboard_get, and clipboard_clear using
ctypes.windll.user32 / ctypes.windll.kernel32 with the CF_UNICODETEXT format.

These are the Phase-4 replacements for the tkinter root.clipboard_*() calls
removed from controller.py (D-20 / T-04-02).

WR-01 (64-bit safety): every Win32 call that returns or accepts a HANDLE /
HGLOBAL / LPVOID / HWND is declared with ``restype``/``argtypes`` at import
time.  Without an explicit ``restype`` ctypes assumes ``c_int`` and truncates
64-bit pointers to 32 bits — a latent memory-corruption bug on the standard
64-bit Python build.

Usage:
    from gui._clipboard import clipboard_set, clipboard_get, clipboard_clear

    clipboard_set("my text")       # write to clipboard
    text = clipboard_get()         # read from clipboard
    clipboard_clear()              # clear the clipboard
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

# CF_UNICODETEXT = 13 (standard Windows clipboard format for Unicode text)
CF_UNICODETEXT: int = 13

# GMEM_MOVEABLE | GMEM_ZEROINIT = 0x0002 | 0x0040 = 0x0042
# SetClipboardData requires GMEM_MOVEABLE memory (the system takes ownership).
_GMEM_FLAGS: int = 0x0042

# ---------------------------------------------------------------------------
# Win32 entry points with explicit signatures (WR-01 / CR-02).
#
# Declared once at import.  Guarded so that importing this module on a
# non-Windows machine (e.g. an open-source contributor running a subset of the
# tests on Linux) does not raise — the individual helpers then simply return
# their best-effort failure value.
# ---------------------------------------------------------------------------
try:
    _k32 = ctypes.windll.kernel32
    _u32 = ctypes.windll.user32

    _k32.GlobalAlloc.restype = ctypes.c_void_p
    _k32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]

    _k32.GlobalLock.restype = ctypes.c_void_p
    _k32.GlobalLock.argtypes = [ctypes.c_void_p]

    _k32.GlobalUnlock.restype = wt.BOOL
    _k32.GlobalUnlock.argtypes = [ctypes.c_void_p]

    _k32.GlobalFree.restype = ctypes.c_void_p
    _k32.GlobalFree.argtypes = [ctypes.c_void_p]

    _u32.OpenClipboard.restype = wt.BOOL
    _u32.OpenClipboard.argtypes = [ctypes.c_void_p]

    _u32.EmptyClipboard.restype = wt.BOOL
    _u32.EmptyClipboard.argtypes = []

    _u32.CloseClipboard.restype = wt.BOOL
    _u32.CloseClipboard.argtypes = []

    _u32.SetClipboardData.restype = ctypes.c_void_p
    _u32.SetClipboardData.argtypes = [wt.UINT, ctypes.c_void_p]

    _u32.GetClipboardData.restype = ctypes.c_void_p
    _u32.GetClipboardData.argtypes = [wt.UINT]
except (AttributeError, OSError):  # pragma: no cover — non-Windows import guard
    _k32 = None
    _u32 = None


def clipboard_set(text: str) -> bool:
    """Write *text* to the Windows clipboard using CF_UNICODETEXT.

    Allocates a GMEM_MOVEABLE global block, copies the text as a wide-char
    string with ``ctypes.memmove`` (no secure-CRT dependency), and hands the
    block to ``SetClipboardData`` inside the OpenClipboard window.  On success
    the system takes ownership of the block; on failure the block is freed and
    the function reports ``False`` so callers never claim a copy that did not
    land (CR-02).

    Args:
        text: The Unicode string to place on the clipboard.

    Returns:
        True only if SetClipboardData succeeded, False on any Win32 failure.
    """
    if _k32 is None or _u32 is None:
        return False
    try:
        # Wide-char buffer (len + 1 for the null terminator), 2 bytes per char.
        src = ctypes.create_unicode_buffer(text)
        size = (len(text) + 1) * 2

        h = _k32.GlobalAlloc(_GMEM_FLAGS, size)
        if not h:
            return False

        p = _k32.GlobalLock(h)
        if not p:
            _k32.GlobalFree(h)
            return False
        # Copy into the locked block, then unlock: the handle must NOT be locked
        # when it is passed to SetClipboardData.
        ctypes.memmove(p, src, size)
        _k32.GlobalUnlock(h)

        if not _u32.OpenClipboard(None):
            _k32.GlobalFree(h)
            return False
        try:
            _u32.EmptyClipboard()
            if not _u32.SetClipboardData(CF_UNICODETEXT, h):
                # The system did NOT take ownership — free the block ourselves.
                _k32.GlobalFree(h)
                return False
            # Ownership transferred to the clipboard — do not touch h anymore.
            return True
        finally:
            _u32.CloseClipboard()
    except Exception:  # noqa: BLE001 — clipboard access is best-effort
        return False


def clipboard_get() -> str:
    """Read the current clipboard text (CF_UNICODETEXT) from Windows.

    Returns:
        The clipboard string, or an empty string if clipboard is empty,
        not text, or any error occurs.
    """
    if _k32 is None or _u32 is None:
        return ""
    try:
        if not _u32.OpenClipboard(None):
            return ""
        try:
            h = _u32.GetClipboardData(CF_UNICODETEXT)
            if not h:
                return ""
            p = _k32.GlobalLock(h)
            if not p:
                return ""
            text = ctypes.cast(p, ctypes.c_wchar_p).value or ""
            _k32.GlobalUnlock(h)
            return text
        finally:
            _u32.CloseClipboard()
    except Exception:  # noqa: BLE001 — clipboard access is best-effort
        return ""


def clipboard_clear() -> None:
    """Clear the Windows clipboard.

    Opens the clipboard, calls EmptyClipboard, then closes it.
    Silently swallows any error (best-effort).
    """
    if _k32 is None or _u32 is None:
        return
    try:
        if not _u32.OpenClipboard(None):
            return
        _u32.EmptyClipboard()
        _u32.CloseClipboard()
    except Exception:  # noqa: BLE001 — clipboard access is best-effort
        pass
