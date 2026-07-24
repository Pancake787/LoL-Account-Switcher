"""Tests for rank_service.py and the QueueRank/RankInfo models.

Unit tests only — requests.get is always mocked.
Never calls the live Riot API.

TDD RED phase: all tests are written before implementation.
"""
from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Minimal stubs so we can import models.py which depends on nothing special,
# and so rank_service.py can be imported without a live requests module hitting
# the network.
# ---------------------------------------------------------------------------


class _MockResponse:
    """Reusable mock requests.Response."""

    def __init__(self, status_code: int, json_data=None, headers=None):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._json_data


# ---------------------------------------------------------------------------
# Test: QueueRank.display property (models.py)
# ---------------------------------------------------------------------------


class TestQueueRankDisplay(unittest.TestCase):
    """Tests for QueueRank.display — no mocking needed, pure dataclass logic."""

    def setUp(self):
        from models import QueueRank
        self.QueueRank = QueueRank

    def test_normal_tier_display(self):
        """Gold II — 47 LP (124S/98N)"""
        q = self.QueueRank(tier="GOLD", division="II", lp=47, wins=124, losses=98)
        self.assertEqual(q.display, "Gold II — 47 LP (124S/98N)")

    def test_normal_tier_silver(self):
        """Silver III — 0 LP (0S/0N)"""
        q = self.QueueRank(tier="SILVER", division="III", lp=0, wins=0, losses=0)
        self.assertEqual(q.display, "Silver III — 0 LP (0S/0N)")

    def test_apex_master_no_division(self):
        """Master — 1000 LP (80S/60N) — division omitted."""
        q = self.QueueRank(tier="MASTER", division="", lp=1000, wins=80, losses=60)
        self.assertEqual(q.display, "Master — 1000 LP (80S/60N)")

    def test_apex_grandmaster_no_division(self):
        """Grandmaster — 500 LP (50S/30N)"""
        q = self.QueueRank(tier="GRANDMASTER", division="", lp=500, wins=50, losses=30)
        self.assertEqual(q.display, "Grandmaster — 500 LP (50S/30N)")

    def test_apex_challenger_no_division(self):
        """Challenger — 2000 LP (200S/100N)"""
        q = self.QueueRank(tier="CHALLENGER", division="", lp=2000, wins=200, losses=100)
        self.assertEqual(q.display, "Challenger — 2000 LP (200S/100N)")

    def test_apex_master_division_i_in_api_but_display_omits(self):
        """API sends rank='I' for Master+ but display must omit it when tier is MASTER."""
        q = self.QueueRank(tier="MASTER", division="I", lp=300, wins=40, losses=20)
        # division is present in the field but APEX_TIERS check ignores it in display
        self.assertEqual(q.display, "Master — 300 LP (40S/20N)")


class TestRankInfoDefaults(unittest.TestCase):
    """Tests for RankInfo dataclass defaults."""

    def setUp(self):
        from models import RankInfo
        self.RankInfo = RankInfo

    def test_defaults_are_none(self):
        ri = self.RankInfo()
        self.assertIsNone(ri.solo)
        self.assertIsNone(ri.flex)
        self.assertIsNone(ri.fetched_at)
        self.assertFalse(ri.stale)


# ---------------------------------------------------------------------------
# Test: rank_service module-level constants and class
# ---------------------------------------------------------------------------


class TestRiotAPIError(unittest.TestCase):
    def test_status_code_attribute(self):
        import rank_service
        err = rank_service.RiotAPIError(404, "not found")
        self.assertEqual(err.status_code, 404)
        self.assertIsInstance(err, Exception)

    def test_message_in_str(self):
        import rank_service
        err = rank_service.RiotAPIError(401, "bad key")
        self.assertIn("bad key", str(err))

    def test_api_key_not_in_error_message(self):
        """T-02-05: api_key must NEVER appear in exception messages."""
        import rank_service
        secret = "RGAPI-very-secret-key-123456"
        # _handle_response should not embed the api_key — it doesn't receive it
        # This test verifies RiotAPIError itself never stores it
        err = rank_service.RiotAPIError(403, "API-Key ungültig oder ohne Berechtigung")
        self.assertNotIn(secret, str(err))
        self.assertNotIn(secret, repr(err))


class TestModuleConstants(unittest.TestCase):
    def test_regional_host(self):
        import rank_service
        self.assertEqual(rank_service.REGIONAL_HOST, "europe.api.riotgames.com")

    def test_platform_hosts_euw(self):
        import rank_service
        self.assertEqual(rank_service.PLATFORM_HOSTS["EUW"], "euw1.api.riotgames.com")

    def test_platform_hosts_eune(self):
        import rank_service
        self.assertEqual(rank_service.PLATFORM_HOSTS["EUNE"], "eun1.api.riotgames.com")

    def test_apex_tiers_set(self):
        import rank_service
        self.assertIn("MASTER", rank_service.APEX_TIERS)
        self.assertIn("GRANDMASTER", rank_service.APEX_TIERS)
        self.assertIn("CHALLENGER", rank_service.APEX_TIERS)

    def test_module_boundary_no_gui(self):
        """rank_service must not import gui/, config, controller, credential_store, riot_client."""
        import rank_service
        import inspect
        source = inspect.getsource(rank_service)
        # Forbidden module names that must not appear in actual import statements.
        # We only check lines that start with 'import' or 'from' (skip comments, docstrings).
        forbidden_modules = ["gui", "config", "controller", "credential_store", "riot_client"]
        for line in source.splitlines():
            stripped = line.strip()
            # Skip comment lines and lines that are not import statements
            if stripped.startswith("#"):
                continue
            if not (stripped.startswith("import ") or stripped.startswith("from ")):
                continue
            for module in forbidden_modules:
                # Check that the forbidden module is not the first token after import/from
                # e.g. "import config" or "from config import ..." or "import config.something"
                import re
                if re.match(rf'^(import|from)\s+{re.escape(module)}(\s|$|\.)', stripped):
                    self.fail(
                        f"rank_service.py must not import '{module}', "
                        f"but found: {stripped!r}"
                    )


# ---------------------------------------------------------------------------
# Test: _handle_response
# ---------------------------------------------------------------------------


class TestHandleResponse(unittest.TestCase):
    def setUp(self):
        import rank_service
        self.rank_service = rank_service

    def test_200_returns_none(self):
        resp = _MockResponse(200)
        result = self.rank_service._handle_response(resp)
        self.assertIsNone(result)

    def test_429_raises_with_retry_after(self):
        resp = _MockResponse(429, headers={"Retry-After": "30"})
        with self.assertRaises(self.rank_service.RiotAPIError) as ctx:
            self.rank_service._handle_response(resp)
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("30", str(ctx.exception))

    def test_429_default_retry_after_when_header_absent(self):
        resp = _MockResponse(429, headers={})
        with self.assertRaises(self.rank_service.RiotAPIError) as ctx:
            self.rank_service._handle_response(resp)
        self.assertEqual(ctx.exception.status_code, 429)
        # Default should reference some retry duration
        self.assertIn("60", str(ctx.exception))

    def test_401_raises(self):
        resp = _MockResponse(401)
        with self.assertRaises(self.rank_service.RiotAPIError) as ctx:
            self.rank_service._handle_response(resp)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_403_raises(self):
        resp = _MockResponse(403)
        with self.assertRaises(self.rank_service.RiotAPIError) as ctx:
            self.rank_service._handle_response(resp)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_404_raises(self):
        resp = _MockResponse(404)
        with self.assertRaises(self.rank_service.RiotAPIError) as ctx:
            self.rank_service._handle_response(resp)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_500_raises(self):
        resp = _MockResponse(500)
        with self.assertRaises(self.rank_service.RiotAPIError) as ctx:
            self.rank_service._handle_response(resp)
        self.assertEqual(ctx.exception.status_code, 500)

    def test_503_raises(self):
        resp = _MockResponse(503)
        with self.assertRaises(self.rank_service.RiotAPIError) as ctx:
            self.rank_service._handle_response(resp)
        self.assertEqual(ctx.exception.status_code, 503)


# ---------------------------------------------------------------------------
# Test: resolve_puuid
# ---------------------------------------------------------------------------


class TestResolvePuuid(unittest.TestCase):
    def setUp(self):
        import rank_service
        self.rank_service = rank_service

    @patch("rank_service.requests")
    def test_correct_url_and_header(self, mock_requests):
        """resolve_puuid calls the correct account-v1 URL with X-Riot-Token header."""
        fake_puuid = "puuid-abc-123"
        mock_resp = _MockResponse(200, {"puuid": fake_puuid})
        mock_requests.get.return_value = mock_resp

        result = self.rank_service.resolve_puuid("Main", "EUW", "RGAPI-testkey")

        mock_requests.get.assert_called_once()
        call_args = mock_requests.get.call_args
        url = call_args[0][0]
        headers = call_args[1]["headers"]

        self.assertIn("europe.api.riotgames.com", url)
        self.assertIn("by-riot-id", url)
        self.assertIn("Main", url)
        self.assertIn("EUW", url)
        self.assertEqual(headers["X-Riot-Token"], "RGAPI-testkey")

    @patch("rank_service.requests")
    def test_returns_puuid_from_json(self, mock_requests):
        """resolve_puuid extracts the puuid field from the response JSON."""
        expected = "xyz-puuid-987"
        mock_requests.get.return_value = _MockResponse(200, {"puuid": expected})

        result = self.rank_service.resolve_puuid("Smurf", "EUNE", "key")
        self.assertEqual(result, expected)

    @patch("rank_service.requests")
    def test_404_raises_riot_api_error(self, mock_requests):
        """resolve_puuid raises RiotAPIError on 404."""
        mock_requests.get.return_value = _MockResponse(404)

        with self.assertRaises(self.rank_service.RiotAPIError) as ctx:
            self.rank_service.resolve_puuid("Unknown", "EUW", "key")
        self.assertEqual(ctx.exception.status_code, 404)

    @patch("rank_service.requests")
    def test_timeout_parameter(self, mock_requests):
        """resolve_puuid passes timeout=10 to requests.get."""
        mock_requests.get.return_value = _MockResponse(200, {"puuid": "p"})
        self.rank_service.resolve_puuid("A", "EUW", "k")
        call_kwargs = mock_requests.get.call_args[1]
        self.assertEqual(call_kwargs.get("timeout"), 10)

    @patch("rank_service.requests")
    def test_game_name_with_space_is_url_encoded(self, mock_requests):
        """CR-01: a game name containing a space is percent-encoded in the URL.

        A real Riot game name like 'Hide on bush' must not produce a raw space
        in the request path; it must be encoded as %20 (not '+').
        """
        mock_requests.get.return_value = _MockResponse(200, {"puuid": "p"})

        self.rank_service.resolve_puuid("Hide on bush", "EUW", "k")

        url = mock_requests.get.call_args[0][0]
        self.assertIn("Hide%20on%20bush", url)
        self.assertNotIn("Hide on bush", url)
        # quote(safe="") encodes spaces as %20, never '+'
        self.assertNotIn("+", url)

    @patch("rank_service.requests")
    def test_tag_line_query_chars_are_encoded(self, mock_requests):
        """CR-01: URL-significant chars in the tag are encoded, blocking query injection.

        A tag like 'EUW?foo=bar' must not inject query parameters into the
        account-v1 request — the '?' and '=' must be percent-encoded.
        """
        mock_requests.get.return_value = _MockResponse(200, {"puuid": "p"})

        self.rank_service.resolve_puuid("Main", "EUW?foo=bar", "k")

        url = mock_requests.get.call_args[0][0]
        # No raw query separator was injected into the path
        self.assertNotIn("?foo=bar", url)
        self.assertIn("%3Ffoo%3Dbar", url)


# ---------------------------------------------------------------------------
# Test: fetch_entries (2-call chain primary)
# ---------------------------------------------------------------------------


class TestFetchEntries(unittest.TestCase):
    def setUp(self):
        import rank_service
        self.rank_service = rank_service

    @patch("rank_service.requests")
    def test_euw_uses_euw1_host(self, mock_requests):
        """fetch_entries for region EUW uses euw1.api.riotgames.com."""
        mock_requests.get.return_value = _MockResponse(200, [])

        self.rank_service.fetch_entries("puuid-123", "EUW", "key")

        url = mock_requests.get.call_args[0][0]
        self.assertIn("euw1.api.riotgames.com", url)
        self.assertIn("by-puuid", url)
        self.assertIn("puuid-123", url)

    @patch("rank_service.requests")
    def test_eune_uses_eun1_host(self, mock_requests):
        """fetch_entries for region EUNE uses eun1.api.riotgames.com."""
        mock_requests.get.return_value = _MockResponse(200, [])

        self.rank_service.fetch_entries("puuid-456", "EUNE", "key")

        url = mock_requests.get.call_args[0][0]
        self.assertIn("eun1.api.riotgames.com", url)

    @patch("rank_service.requests")
    def test_unknown_region_defaults_to_euw(self, mock_requests):
        """Unknown region defaults to EUW platform host."""
        mock_requests.get.return_value = _MockResponse(200, [])

        self.rank_service.fetch_entries("p", "NA", "key")

        url = mock_requests.get.call_args[0][0]
        self.assertIn("euw1.api.riotgames.com", url)

    @patch("rank_service.requests")
    def test_returns_json_list(self, mock_requests):
        """fetch_entries returns the JSON list from the response."""
        expected = [{"queueType": "RANKED_SOLO_5x5"}]
        mock_requests.get.return_value = _MockResponse(200, expected)

        result = self.rank_service.fetch_entries("p", "EUW", "k")
        self.assertEqual(result, expected)

    @patch("rank_service.requests")
    def test_correct_header(self, mock_requests):
        """fetch_entries sends X-Riot-Token header."""
        mock_requests.get.return_value = _MockResponse(200, [])

        self.rank_service.fetch_entries("p", "EUW", "my-api-key")

        headers = mock_requests.get.call_args[1]["headers"]
        self.assertEqual(headers["X-Riot-Token"], "my-api-key")

    @patch("rank_service.requests")
    def test_404_raises(self, mock_requests):
        """fetch_entries raises RiotAPIError on 404."""
        mock_requests.get.return_value = _MockResponse(404)

        with self.assertRaises(self.rank_service.RiotAPIError) as ctx:
            self.rank_service.fetch_entries("p", "EUW", "k")
        self.assertEqual(ctx.exception.status_code, 404)

    @patch("rank_service.requests")
    def test_region_case_insensitive(self, mock_requests):
        """region lookup is case-insensitive (euw → EUW)."""
        mock_requests.get.return_value = _MockResponse(200, [])

        self.rank_service.fetch_entries("p", "euw", "k")

        url = mock_requests.get.call_args[0][0]
        self.assertIn("euw1.api.riotgames.com", url)


# ---------------------------------------------------------------------------
# Test: fetch_summoner_id (3-call fallback step 2a)
# ---------------------------------------------------------------------------


class TestFetchSummonerId(unittest.TestCase):
    def setUp(self):
        import rank_service
        self.rank_service = rank_service

    @patch("rank_service.requests")
    def test_returns_summoner_id(self, mock_requests):
        """fetch_summoner_id returns the 'id' field from summoner-v4."""
        mock_requests.get.return_value = _MockResponse(200, {"id": "enc-summ-id-xyz"})

        result = self.rank_service.fetch_summoner_id("puuid-abc", "EUW", "key")
        self.assertEqual(result, "enc-summ-id-xyz")

    @patch("rank_service.requests")
    def test_missing_id_field_raises(self, mock_requests):
        """fetch_summoner_id raises if 'id' field absent (Pitfall 1 / Aug-2025 bug)."""
        mock_requests.get.return_value = _MockResponse(200, {"accountId": "old", "puuid": "p"})

        with self.assertRaises(Exception):
            self.rank_service.fetch_summoner_id("puuid-abc", "EUW", "key")

    @patch("rank_service.requests")
    def test_correct_endpoint_url(self, mock_requests):
        """fetch_summoner_id calls summoner-v4/summoners/by-puuid/{puuid}."""
        mock_requests.get.return_value = _MockResponse(200, {"id": "sid"})

        self.rank_service.fetch_summoner_id("my-puuid", "EUW", "k")

        url = mock_requests.get.call_args[0][0]
        self.assertIn("summoner", url)
        self.assertIn("by-puuid", url)
        self.assertIn("my-puuid", url)


# ---------------------------------------------------------------------------
# Test: fetch_entries_by_summoner (3-call fallback step 2b)
# ---------------------------------------------------------------------------


class TestFetchEntriesBySummoner(unittest.TestCase):
    def setUp(self):
        import rank_service
        self.rank_service = rank_service

    @patch("rank_service.requests")
    def test_calls_by_summoner_endpoint(self, mock_requests):
        """fetch_entries_by_summoner calls league-v4/entries/by-summoner/{id}."""
        mock_requests.get.return_value = _MockResponse(200, [])

        self.rank_service.fetch_entries_by_summoner("summ-id-123", "EUW", "key")

        url = mock_requests.get.call_args[0][0]
        self.assertIn("by-summoner", url)
        self.assertIn("summ-id-123", url)

    @patch("rank_service.requests")
    def test_returns_json_list(self, mock_requests):
        """fetch_entries_by_summoner returns the JSON list."""
        expected = [{"queueType": "RANKED_FLEX_SR"}]
        mock_requests.get.return_value = _MockResponse(200, expected)

        result = self.rank_service.fetch_entries_by_summoner("s", "EUW", "k")
        self.assertEqual(result, expected)


# ---------------------------------------------------------------------------
# Test: parse_entries
# ---------------------------------------------------------------------------


class TestParseEntries(unittest.TestCase):
    def setUp(self):
        import rank_service
        self.rank_service = rank_service

    def test_empty_list_returns_unranked(self):
        """parse_entries([]) → RankInfo with solo=None, flex=None."""
        ri = self.rank_service.parse_entries([])
        self.assertIsNone(ri.solo)
        self.assertIsNone(ri.flex)

    def test_fetched_at_is_set(self):
        """parse_entries sets fetched_at to a non-None float."""
        ri = self.rank_service.parse_entries([])
        self.assertIsNotNone(ri.fetched_at)
        self.assertIsInstance(ri.fetched_at, float)

    def test_solo_entry_populates_solo(self):
        """RANKED_SOLO_5x5 entry → rank.solo populated, flex=None."""
        entries = [{
            "queueType": "RANKED_SOLO_5x5",
            "tier": "GOLD",
            "rank": "II",
            "leaguePoints": 47,
            "wins": 124,
            "losses": 98,
        }]
        ri = self.rank_service.parse_entries(entries)
        self.assertIsNotNone(ri.solo)
        self.assertIsNone(ri.flex)
        self.assertEqual(ri.solo.tier, "GOLD")
        self.assertEqual(ri.solo.division, "II")
        self.assertEqual(ri.solo.lp, 47)
        self.assertEqual(ri.solo.wins, 124)
        self.assertEqual(ri.solo.losses, 98)

    def test_solo_display_string(self):
        """parse_entries GOLD II 47 LP → display 'Gold II — 47 LP (124S/98N)'."""
        entries = [{
            "queueType": "RANKED_SOLO_5x5",
            "tier": "GOLD",
            "rank": "II",
            "leaguePoints": 47,
            "wins": 124,
            "losses": 98,
        }]
        ri = self.rank_service.parse_entries(entries)
        self.assertEqual(ri.solo.display, "Gold II — 47 LP (124S/98N)")

    def test_flex_entry_populates_flex(self):
        """RANKED_FLEX_SR entry → rank.flex populated, solo=None."""
        entries = [{
            "queueType": "RANKED_FLEX_SR",
            "tier": "SILVER",
            "rank": "III",
            "leaguePoints": 20,
            "wins": 30,
            "losses": 25,
        }]
        ri = self.rank_service.parse_entries(entries)
        self.assertIsNone(ri.solo)
        self.assertIsNotNone(ri.flex)
        self.assertEqual(ri.flex.tier, "SILVER")

    def test_both_queues_populated_independently(self):
        """Both SOLO and FLEX entries → both queues populated independently."""
        entries = [
            {
                "queueType": "RANKED_SOLO_5x5",
                "tier": "GOLD",
                "rank": "I",
                "leaguePoints": 75,
                "wins": 100,
                "losses": 80,
            },
            {
                "queueType": "RANKED_FLEX_SR",
                "tier": "PLATINUM",
                "rank": "IV",
                "leaguePoints": 10,
                "wins": 40,
                "losses": 35,
            },
        ]
        ri = self.rank_service.parse_entries(entries)
        self.assertIsNotNone(ri.solo)
        self.assertIsNotNone(ri.flex)
        self.assertEqual(ri.solo.tier, "GOLD")
        self.assertEqual(ri.flex.tier, "PLATINUM")

    def test_master_tier_division_empty(self):
        """MASTER entry → division='' (APEX tier), display omits division."""
        entries = [{
            "queueType": "RANKED_SOLO_5x5",
            "tier": "MASTER",
            "rank": "I",        # API sends "I" but display omits it
            "leaguePoints": 1000,
            "wins": 80,
            "losses": 60,
        }]
        ri = self.rank_service.parse_entries(entries)
        self.assertEqual(ri.solo.division, "")
        self.assertEqual(ri.solo.display, "Master — 1000 LP (80S/60N)")

    def test_grandmaster_tier(self):
        """GRANDMASTER → division='', display without division."""
        entries = [{
            "queueType": "RANKED_SOLO_5x5",
            "tier": "GRANDMASTER",
            "rank": "I",
            "leaguePoints": 500,
            "wins": 50,
            "losses": 30,
        }]
        ri = self.rank_service.parse_entries(entries)
        self.assertEqual(ri.solo.division, "")
        self.assertIn("Grandmaster", ri.solo.display)
        self.assertNotIn("I", ri.solo.display.split("—")[0])  # No division before dash

    def test_challenger_tier(self):
        """CHALLENGER → division='', display without division."""
        entries = [{
            "queueType": "RANKED_SOLO_5x5",
            "tier": "CHALLENGER",
            "rank": "I",
            "leaguePoints": 2000,
            "wins": 200,
            "losses": 100,
        }]
        ri = self.rank_service.parse_entries(entries)
        self.assertEqual(ri.solo.division, "")
        self.assertEqual(ri.solo.display, "Challenger — 2000 LP (200S/100N)")

    def test_unknown_queue_type_ignored(self):
        """Unknown queueType entries are ignored (solo/flex stay None)."""
        entries = [{
            "queueType": "RANKED_TFT",
            "tier": "GOLD",
            "rank": "II",
            "leaguePoints": 50,
            "wins": 10,
            "losses": 5,
        }]
        ri = self.rank_service.parse_entries(entries)
        self.assertIsNone(ri.solo)
        self.assertIsNone(ri.flex)

    def test_stale_flag_defaults_false(self):
        """parse_entries sets stale=False on fresh data."""
        ri = self.rank_service.parse_entries([])
        self.assertFalse(ri.stale)


if __name__ == "__main__":
    unittest.main()
