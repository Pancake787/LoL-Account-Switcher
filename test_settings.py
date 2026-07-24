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


if __name__ == "__main__":
    unittest.main()
