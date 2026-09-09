"""Tests for the individual data sources behind the analyst tools.

Each of these talks to an external API. What matters offline is the shape
around the call: how a missing key, an outage, or an empty response is
reported, and the filtering that decides whether a post or series is even
relevant to the symbol being analyzed.
"""

from __future__ import annotations

import unittest
from unittest import mock

from tradingagents.dataflows import defillama_utils, earnings_utils, macro_utils
from tradingagents.dataflows import reddit_utils


class FredCredentialTests(unittest.TestCase):
    def test_a_configured_key_is_used(self):
        with mock.patch.object(macro_utils, "get_api_key", lambda *a: "fred-key"):
            self.assertEqual(macro_utils.get_fred_api_key(), "fred-key")

    def test_a_lookup_failure_falls_back_to_the_environment(self):
        with mock.patch.object(
            macro_utils, "get_api_key", side_effect=RuntimeError("vault locked")
        ), mock.patch.dict("os.environ", {"FRED_API_KEY": "from-env"}):
            self.assertEqual(macro_utils.get_fred_api_key(), "from-env")

    def test_no_key_anywhere_reads_as_absent(self):
        with mock.patch.object(macro_utils, "get_api_key", lambda *a: None), \
             mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(macro_utils.get_fred_api_key())


class FredVintageTests(unittest.TestCase):
    """FRED rejects a vintage in the future, and the series' own calendar is
    Central time."""

    def test_a_past_date_is_kept(self):
        self.assertEqual(macro_utils._fred_vintage_date("2020-01-01"), "2020-01-01")

    def test_a_future_date_is_clamped_to_today(self):
        clamped = macro_utils._fred_vintage_date("2999-01-01")

        self.assertNotEqual(clamped, "2999-01-01")
        self.assertLess(clamped, "2999-01-01")


class FredFetchTests(unittest.TestCase):
    def test_a_missing_key_is_reported_rather_than_requested(self):
        with mock.patch.object(macro_utils, "get_fred_api_key", lambda: None):
            result = macro_utils.get_fred_data("FEDFUNDS", "2026-01-01", "2026-06-01")

        self.assertIn("error", result)
        self.assertIn("FRED API key", result["error"])

    def test_a_successful_response_is_returned(self):
        response = mock.MagicMock()
        response.json.return_value = {"observations": [{"value": "5.0"}]}

        with mock.patch.object(macro_utils, "get_fred_api_key", lambda: "k"), \
             mock.patch.object(macro_utils.requests, "get", lambda *a, **k: response):
            result = macro_utils.get_fred_data("FEDFUNDS", "2026-01-01", "2026-06-01")

        self.assertEqual(result["observations"][0]["value"], "5.0")

    def test_an_outage_is_reported_rather_than_raised(self):
        def explode(*_a, **_k):
            raise RuntimeError("connection reset")

        with mock.patch.object(macro_utils, "get_fred_api_key", lambda: "k"), \
             mock.patch.object(macro_utils.requests, "get", explode):
            result = macro_utils.get_fred_data("FEDFUNDS", "2026-01-01", "2026-06-01")

        self.assertIn("error", result)
        self.assertIn("connection reset", result["error"])

    def test_the_request_pins_the_vintage_to_both_ends(self):
        captured = {}

        def capture(_url, params=None, timeout=None):
            captured.update(params or {})
            response = mock.MagicMock()
            response.json.return_value = {}
            return response

        with mock.patch.object(macro_utils, "get_fred_api_key", lambda: "k"), \
             mock.patch.object(macro_utils.requests, "get", capture):
            macro_utils.get_fred_data("FEDFUNDS", "2026-01-01", "2026-06-01")

        self.assertEqual(captured["realtime_start"], captured["realtime_end"])
        self.assertEqual(captured["series_id"], "FEDFUNDS")


class MacroReportTests(unittest.TestCase):
    def _no_key(self):
        return mock.patch.object(macro_utils, "get_fred_api_key", lambda: None)

    def test_the_yield_curve_reports_a_missing_key(self):
        with self._no_key():
            report = macro_utils.get_treasury_yield_curve("2026-09-09")

        self.assertIsInstance(report, str)
        self.assertTrue(report.strip())

    def test_the_indicator_report_survives_a_missing_key(self):
        with self._no_key():
            report = macro_utils.get_economic_indicators_report("2026-09-09")

        self.assertIsInstance(report, str)

    def test_the_macro_summary_survives_a_missing_key(self):
        with self._no_key():
            report = macro_utils.get_macro_economic_summary("2026-09-09")

        self.assertIsInstance(report, str)

    def test_the_fed_calendar_survives_a_missing_key(self):
        with self._no_key():
            self.assertIsInstance(
                macro_utils.get_fed_calendar_and_minutes("2026-09-09"), str
            )


class RedditSearchTermTests(unittest.TestCase):
    def setUp(self):
        reddit_utils._SEARCH_TERMS_CACHE.clear()
        self.addCleanup(reddit_utils._SEARCH_TERMS_CACHE.clear)

    def test_a_ticker_yields_itself_and_its_cashtag(self):
        with mock.patch.object(reddit_utils, "get_company_name", lambda t: t):
            terms = reddit_utils.get_search_terms("NVDA")

        self.assertIn("NVDA", terms)
        self.assertIn("$NVDA", terms)

    def test_a_company_name_is_added_and_trimmed_of_suffixes(self):
        with mock.patch.object(
            reddit_utils, "get_company_name", lambda _t: "NVIDIA Corporation"
        ):
            terms = reddit_utils.get_search_terms("NVDA")

        self.assertIn("NVIDIA Corporation", terms)
        self.assertIn("NVIDIA", terms)

    def test_alias_names_become_separate_terms(self):
        with mock.patch.object(
            reddit_utils, "get_company_name", lambda _t: "Alphabet OR Google"
        ):
            terms = reddit_utils.get_search_terms("GOOGL")

        self.assertIn("Alphabet", terms)
        self.assertIn("Google", terms)

    def test_an_empty_ticker_yields_no_terms(self):
        self.assertEqual(reddit_utils.get_search_terms(""), [])

    def test_terms_are_deduplicated(self):
        with mock.patch.object(reddit_utils, "get_company_name", lambda _t: "NVDA"):
            terms = reddit_utils.get_search_terms("NVDA")

        self.assertEqual(len(terms), len(set(terms)))

    def test_the_result_is_cached_per_ticker(self):
        calls = {"count": 0}

        def counted(_ticker):
            calls["count"] += 1
            return "NVIDIA Corp"

        with mock.patch.object(reddit_utils, "get_company_name", counted):
            reddit_utils.get_search_terms("NVDA")
            reddit_utils.get_search_terms("NVDA")

        self.assertEqual(calls["count"], 1)


class RedditRelevanceTests(unittest.TestCase):
    """Reddit is noisy; a two-letter ticker matches almost anything."""

    TERMS = ["NVDA", "$NVDA", "NVIDIA"]

    def test_a_post_naming_the_company_is_relevant(self):
        self.assertTrue(
            reddit_utils._post_relevant_to_company(
                "NVIDIA earnings", "strong quarter", self.TERMS
            )
        )

    def test_a_post_naming_the_ticker_is_relevant(self):
        self.assertTrue(
            reddit_utils._post_relevant_to_company("NVDA up", "", self.TERMS)
        )

    def test_an_unrelated_post_is_not_relevant(self):
        self.assertFalse(
            reddit_utils._post_relevant_to_company(
                "Bread recipes", "flour and water", self.TERMS
            )
        )

    def test_a_term_inside_a_word_does_not_match(self):
        self.assertFalse(
            reddit_utils._post_relevant_to_company("NVDAX fund", "", ["NVDA"])
        )

    def test_a_very_short_ticker_only_matches_as_a_cashtag(self):
        """Otherwise 'F' or 'A' matches nearly every post."""
        self.assertFalse(
            reddit_utils._post_relevant_to_company("A day of trading", "", ["F"])
        )
        self.assertTrue(
            reddit_utils._post_relevant_to_company("$F is up", "", ["$F"])
        )

    def test_no_terms_lets_everything_through(self):
        self.assertTrue(reddit_utils._post_relevant_to_company("anything", "", []))

    def test_matching_ignores_case(self):
        self.assertTrue(
            reddit_utils._post_relevant_to_company("nvidia rally", "", ["NVIDIA"])
        )


class EarningsCalendarTests(unittest.TestCase):
    def test_a_missing_key_is_reported(self):
        with mock.patch.object(
            earnings_utils, "get_earnings_calendar_api_key", lambda: None
        ):
            report = earnings_utils.get_finnhub_earnings_calendar("NVDA", "2026-09-01", "2026-09-30")

        self.assertIsInstance(report, str)
        self.assertTrue(report.strip())

    def test_an_outage_is_reported_rather_than_raised(self):
        def explode(*_a, **_k):
            raise RuntimeError("connection reset")

        with mock.patch.object(
            earnings_utils, "get_earnings_calendar_api_key", lambda: "k"
        ), mock.patch.object(earnings_utils.requests, "get", explode):
            report = earnings_utils.get_finnhub_earnings_calendar("NVDA", "2026-09-01", "2026-09-30")

        self.assertIsInstance(report, str)

    def test_crypto_symbols_get_their_own_calendar_equivalent(self):
        report = earnings_utils.get_crypto_earnings_equivalent("BTC/USD", "2026-09-01", "2026-09-30")

        self.assertIsInstance(report, str)
        self.assertTrue(report.strip())

    def test_the_dispatcher_routes_crypto_away_from_finnhub(self):
        with mock.patch.object(
            earnings_utils,
            "get_finnhub_earnings_calendar",
            lambda *a, **k: "equity path",
        ), mock.patch.object(
            earnings_utils,
            "get_crypto_earnings_equivalent",
            lambda *a, **k: "crypto path",
        ):
            self.assertEqual(
                earnings_utils.get_earnings_calendar_data("BTC/USD", "2026-09-01", "2026-09-30"),
                "crypto path",
            )
            self.assertEqual(
                earnings_utils.get_earnings_calendar_data("NVDA", "2026-09-01", "2026-09-30"),
                "equity path",
            )

    def test_the_surprise_analysis_survives_a_missing_key(self):
        with mock.patch.object(
            earnings_utils, "get_earnings_calendar_api_key", lambda: None
        ):
            self.assertIsInstance(
                earnings_utils.get_earnings_surprises_analysis("NVDA", "2026-09-09"),
                str,
            )


class DefiLlamaTests(unittest.TestCase):
    def test_an_outage_is_reported_rather_than_raised(self):
        with mock.patch.object(
            defillama_utils, "_fetch_json", side_effect=RuntimeError("gateway timeout")
        ):
            report = defillama_utils.get_fundamentals("ETH/USD")

        self.assertIsInstance(report, str)
        self.assertTrue(report.strip())

    def test_an_unknown_symbol_is_reported(self):
        with mock.patch.object(defillama_utils, "_get_protocols", lambda: []):
            report = defillama_utils.get_fundamentals("NOTACOIN/USD")

        self.assertIsInstance(report, str)

    def test_a_known_protocol_is_located_by_symbol(self):
        protocols = [
            {"slug": "uniswap", "symbol": "UNI", "name": "Uniswap", "tvl": 1_000_000}
        ]

        with mock.patch.object(defillama_utils, "_get_protocols", lambda: protocols):
            slug, name = defillama_utils._find_slug("UNI")

        self.assertEqual(slug, "uniswap")
        self.assertEqual(name, "Uniswap")

    def test_an_unmatched_symbol_locates_nothing(self):
        with mock.patch.object(defillama_utils, "_get_protocols", lambda: []):
            slug, name = defillama_utils._find_slug("NOPE")

        self.assertIsNone(slug)
        self.assertIsNone(name)


if __name__ == "__main__":
    unittest.main()


class RedditOfflineFetchTests(unittest.TestCase):
    """The offline corpus is jsonl-per-subreddit, filtered by day."""

    def setUp(self):
        import json
        import tempfile
        from pathlib import Path

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.category = self.root / "company_news"
        self.category.mkdir(parents=True)

        posts = [
            {
                "created_utc": 1_767_225_600,  # 2026-01-01
                "title": "NVIDIA beats expectations",
                "selftext": "Strong quarter for NVDA.",
                "score": 100,
                "ups": 100,
                "num_comments": 20,
                "url": "https://reddit.test/1",
            },
            {
                "created_utc": 1_767_225_600,
                "title": "Bread recipes",
                "selftext": "Flour and water.",
                "score": 5,
                "ups": 5,
                "num_comments": 1,
                "url": "https://reddit.test/2",
            },
            {
                "created_utc": 1_767_312_000,  # 2026-01-02
                "title": "NVIDIA the next day",
                "selftext": "More NVDA news.",
                "score": 50,
                "ups": 50,
                "num_comments": 10,
                "url": "https://reddit.test/3",
            },
        ]
        with (self.category / "wallstreetbets.jsonl").open("w", encoding="utf-8") as fh:
            for post in posts:
                fh.write(json.dumps(post) + "\n")

        reddit_utils._SEARCH_TERMS_CACHE.clear()
        self.addCleanup(reddit_utils._SEARCH_TERMS_CACHE.clear)

    def _fetch(self, date, query=None, max_limit=10):
        with mock.patch.object(reddit_utils, "get_company_name", lambda t: t):
            return reddit_utils.fetch_top_from_category(
                "company_news", date, max_limit, query=query, data_path=str(self.root)
            )

    def test_only_that_day_is_returned(self):
        posts = self._fetch("2026-01-01")

        self.assertTrue(posts)
        self.assertTrue(all("next day" not in post["title"] for post in posts))

    def test_a_query_filters_to_relevant_posts(self):
        posts = self._fetch("2026-01-01", query="NVDA")

        titles = [post["title"] for post in posts]
        self.assertIn("NVIDIA beats expectations", titles)
        self.assertNotIn("Bread recipes", titles)

    def test_without_a_query_everything_that_day_is_returned(self):
        posts = self._fetch("2026-01-01")

        self.assertEqual(len(posts), 2)

    def test_a_day_with_no_posts_returns_nothing(self):
        self.assertEqual(self._fetch("2020-05-05"), [])

    def test_a_limit_below_the_file_count_is_refused(self):
        """It could not fetch anything, so it says so rather than silently
        returning an empty list."""
        with self.assertRaises(ValueError):
            self._fetch("2026-01-01", max_limit=0)

    def test_non_jsonl_files_are_ignored(self):
        (self.category / "notes.txt").write_text("ignore me", encoding="utf-8")

        self.assertTrue(self._fetch("2026-01-01"))


class RedditOnlineFetchTests(unittest.TestCase):
    def test_no_credentials_yields_no_posts(self):
        with mock.patch.object(
            reddit_utils, "_get_reddit_client", lambda: None, create=True
        ):
            result = reddit_utils.fetch_top_from_category_online(
                "company_news", "2026-01-01", "2026-01-02", 5, query="NVDA"
            )

        self.assertEqual(result, [])

    def test_an_api_failure_yields_no_posts(self):
        """Reddit is a supplementary source; an outage must not fail a run."""
        with mock.patch.object(
            reddit_utils,
            "praw",
            mock.MagicMock(side_effect=RuntimeError("api down")),
            create=True,
        ):
            result = reddit_utils.fetch_top_from_category_online(
                "company_news", "2026-01-01", "2026-01-02", 5, query="NVDA"
            )

        self.assertIsInstance(result, list)
