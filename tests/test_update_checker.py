"""Tests for update_checker.py (ONBOARD-03).

Unit tests only — requests.get is always mocked. Never calls the live
GitHub API.
"""
from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch

import update_checker


class _MockResponse:
    """Reusable mock requests.Response."""

    def __init__(self, status_code: int, json_data=None):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}

    def json(self):
        return self._json_data


class TestParseSemver(unittest.TestCase):
    def test_parses_v_prefixed_tag(self):
        self.assertEqual(update_checker._parse_semver("v2.2.0"), (2, 2, 0))

    def test_parses_bare_tag(self):
        self.assertEqual(update_checker._parse_semver("2.10.3"), (2, 10, 3))

    def test_comparison_respects_numeric_not_lexical_order(self):
        self.assertGreater(update_checker._parse_semver("2.10.0"), update_checker._parse_semver("2.9.0"))


class TestCheckForUpdateThrottle(unittest.TestCase):
    def test_ttl_not_elapsed_returns_none_without_network_call(self):
        with patch("update_checker.requests.get") as mock_get:
            result = update_checker.check_for_update("2.1.0", time.time())
        self.assertIsNone(result)
        mock_get.assert_not_called()

    def test_never_checked_before_elapses_ttl(self):
        """last_checked_at=0.0 (never checked) must NOT be throttled."""
        with patch("update_checker.requests.get") as mock_get:
            mock_get.return_value = _MockResponse(200, {"tag_name": "v2.1.0", "html_url": "https://x"})
            update_checker.check_for_update("2.1.0", 0.0)
        mock_get.assert_called_once()


class TestCheckForUpdateResult(unittest.TestCase):
    def _elapsed_ts(self) -> float:
        return time.time() - update_checker.CHECK_TTL_S - 1

    def test_returns_update_dict_when_newer_tag_exists(self):
        with patch("update_checker.requests.get") as mock_get:
            mock_get.return_value = _MockResponse(
                200, {"tag_name": "v2.2.0", "html_url": "https://github.com/x/releases/v2.2.0"}
            )
            result = update_checker.check_for_update("2.1.0", self._elapsed_ts())
        self.assertEqual(
            result, {"tag_name": "v2.2.0", "html_url": "https://github.com/x/releases/v2.2.0"}
        )

    def test_returns_none_when_tag_equals_current(self):
        with patch("update_checker.requests.get") as mock_get:
            mock_get.return_value = _MockResponse(200, {"tag_name": "v2.1.0", "html_url": "https://x"})
            result = update_checker.check_for_update("2.1.0", self._elapsed_ts())
        self.assertIsNone(result)

    def test_returns_none_when_tag_older_than_current(self):
        with patch("update_checker.requests.get") as mock_get:
            mock_get.return_value = _MockResponse(200, {"tag_name": "v2.0.0", "html_url": "https://x"})
            result = update_checker.check_for_update("2.1.0", self._elapsed_ts())
        self.assertIsNone(result)

    def test_malformed_tag_does_not_raise(self):
        with patch("update_checker.requests.get") as mock_get:
            mock_get.return_value = _MockResponse(200, {"tag_name": "not-a-version", "html_url": "https://x"})
            result = update_checker.check_for_update("2.1.0", self._elapsed_ts())
        self.assertIsNone(result)

    def test_empty_tag_name_returns_none(self):
        with patch("update_checker.requests.get") as mock_get:
            mock_get.return_value = _MockResponse(200, {"tag_name": "", "html_url": "https://x"})
            result = update_checker.check_for_update("2.1.0", self._elapsed_ts())
        self.assertIsNone(result)


class TestCheckForUpdateFailureModes(unittest.TestCase):
    def _elapsed_ts(self) -> float:
        return time.time() - update_checker.CHECK_TTL_S - 1

    def test_connection_error_returns_none(self):
        import requests.exceptions

        with patch("update_checker.requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.ConnectionError("boom")
            result = update_checker.check_for_update("2.1.0", self._elapsed_ts())
        self.assertIsNone(result)

    def test_timeout_returns_none(self):
        import requests.exceptions

        with patch("update_checker.requests.get") as mock_get:
            mock_get.side_effect = requests.exceptions.Timeout("boom")
            result = update_checker.check_for_update("2.1.0", self._elapsed_ts())
        self.assertIsNone(result)

    def test_rate_limit_403_returns_none(self):
        with patch("update_checker.requests.get") as mock_get:
            mock_get.return_value = _MockResponse(403)
            result = update_checker.check_for_update("2.1.0", self._elapsed_ts())
        self.assertIsNone(result)

    def test_server_error_500_returns_none(self):
        with patch("update_checker.requests.get") as mock_get:
            mock_get.return_value = _MockResponse(500)
            result = update_checker.check_for_update("2.1.0", self._elapsed_ts())
        self.assertIsNone(result)

    def test_no_authorization_header_ever_sent(self):
        """T-08-05: never ship an Authorization header (no token in a public client)."""
        with patch("update_checker.requests.get") as mock_get:
            mock_get.return_value = _MockResponse(200, {"tag_name": "v2.1.0", "html_url": "https://x"})
            update_checker.check_for_update("2.1.0", self._elapsed_ts())
        _args, kwargs = mock_get.call_args
        self.assertNotIn("headers", kwargs)


if __name__ == "__main__":
    unittest.main()
