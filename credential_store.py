"""Keyring wrapper for Windows Credential Manager.

Provides save / get / delete operations for account credentials using the
Windows Credential Manager (WCM) via the keyring library.

Security rules (T-01-04, T-01-05, SEC-1):
- Passwords are stored ONLY in Windows Credential Manager — never on disk.
- Passwords are fetched ON DEMAND — never cached in application state.
- save() uses delete-then-set ordering to avoid the Windows duplicate-key bug
  (COMMON-2 from RESEARCH.md Pattern 4).
"""
from __future__ import annotations

import pathlib

import keyring
import keyring.errors

import config

SERVICE = "lol-switcher"


def save(username: str, password: str) -> None:
    """Store a password in Windows Credential Manager.

    Uses delete-then-set ordering (COMMON-2) to prevent the Windows
    duplicate-entry error that occurs when the same username already exists
    under this service name.

    Args:
        username: The Riot username (credential key).
        password: The plaintext password to store in WCM via DPAPI.
    """
    try:
        keyring.delete_password(SERVICE, username)
    except keyring.errors.PasswordDeleteError:
        pass  # Entry did not exist yet — that is fine
    keyring.set_password(SERVICE, username, password)


def get(username: str) -> str:
    """Retrieve a password from Windows Credential Manager.

    Args:
        username: The Riot username to look up.

    Returns:
        The stored password, or an empty string if none is found.
    """
    return keyring.get_password(SERVICE, username) or ""


def delete(username: str) -> None:
    """Remove a credential from Windows Credential Manager.

    Silently does nothing if the username does not exist.

    Args:
        username: The Riot username whose credential should be removed.
    """
    try:
        keyring.delete_password(SERVICE, username)
    except keyring.errors.PasswordDeleteError:
        pass  # Already absent — nothing to do


# ---------------------------------------------------------------------------
# Phase 2 — API key storage (D-26)
# Service name is intentionally distinct from SERVICE to keep account
# passwords and the Riot API key in separate WCM credential entries.
# ---------------------------------------------------------------------------

API_SERVICE = "lol-switcher-api"
API_USERNAME = "riot_api_key"


def save_api_key(key: str) -> None:
    """Store the Riot API key in Windows Credential Manager.

    Uses delete-then-set ordering (COMMON-2) matching save() above to prevent
    the Windows duplicate-key bug when the same entry already exists.

    Security: the key value is never logged or printed.

    Args:
        key: The personal Riot API key to store in WCM via DPAPI.
    """
    try:
        keyring.delete_password(API_SERVICE, API_USERNAME)
    except keyring.errors.PasswordDeleteError:
        pass  # Entry did not exist yet — that is fine
    keyring.set_password(API_SERVICE, API_USERNAME, key)


def get_api_key() -> str:
    """Retrieve the Riot API key.

    Resolution order:
      1. The DPAPI-encrypted key file (``set_api_key_file`` / ``set-api-key`` CLI) —
         the hard-set, persistent key.
      2. Windows Credential Manager (backward compat with the old settings dialog).

    Returns:
        The stored API key, or an empty string if none is found.
    """
    file_key = _read_api_key_file()
    if file_key:
        return file_key
    return keyring.get_password(API_SERVICE, API_USERNAME) or ""


def delete_api_key() -> None:
    """Remove the Riot API key from Windows Credential Manager.

    Silently does nothing if the entry does not exist. Does NOT touch the
    DPAPI key file (use ``delete_api_key_file`` for that).
    """
    try:
        keyring.delete_password(API_SERVICE, API_USERNAME)
    except keyring.errors.PasswordDeleteError:
        pass  # Already absent — nothing to do


# ---------------------------------------------------------------------------
# DPAPI-encrypted key file (hard-set personal key, no settings dialog)
#
# The key is stored in a file under config.APP_DIR (%APPDATA%\LoLSwitcher\),
# encrypted with the Windows Data Protection API (DPAPI) — the same per-user
# encryption the Credential Manager uses. The blob is decryptable ONLY by the
# current Windows user account; it is never written as plaintext, and the file
# lives outside the git repository.
#
# DPAPI is reached via ctypes -> crypt32.dll, so there is no extra dependency
# and no PyInstaller hiddenimport is required.
# ---------------------------------------------------------------------------

#: Path to the encrypted key file. Derived from config.APP_DIR at call time so
#: tests that redirect config.APP_DIR to a temp dir stay hermetic.
def _api_key_file() -> pathlib.Path:
    return config.APP_DIR / "riot_api_key.dat"


def _dpapi(data: bytes, *, encrypt: bool) -> bytes:
    """Encrypt or decrypt ``data`` with Windows DPAPI (CryptProtectData/Unprotect).

    User-scoped (no machine flag): only the current Windows user can decrypt.
    Raises OSError on failure.
    """
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _Blob()
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    _CRYPTPROTECT_UI_FORBIDDEN = 0x01
    ok = fn(ctypes.byref(blob_in), None, None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    if not ok:
        raise OSError("DPAPI operation failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def set_api_key_file(key: str) -> None:
    """Encrypt the Riot API key with DPAPI and write it to the key file.

    Creates config.APP_DIR if needed. The key is never written as plaintext.

    Args:
        key: The personal Riot API key to store.
    """
    path = _api_key_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_dpapi(key.encode("utf-8"), encrypt=True))


def _read_api_key_file() -> str:
    """Return the decrypted API key from the file, or '' if absent/unreadable."""
    path = _api_key_file()
    if not path.exists():
        return ""
    try:
        return _dpapi(path.read_bytes(), encrypt=False).decode("utf-8")
    except Exception:  # noqa: BLE001 — corrupt/foreign-user blob -> treat as no key
        return ""


def delete_api_key_file() -> None:
    """Remove the DPAPI key file. Silently does nothing if it does not exist."""
    try:
        _api_key_file().unlink()
    except FileNotFoundError:
        pass
