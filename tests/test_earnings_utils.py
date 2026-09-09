"""Tests for the earnings calendar surface.

Stocks route to Finnhub's earnings calendar; crypto tickers, which have no
earnings, route to an events briefing instead. Both answers land directly
in a prompt, so a failure has to be prose the model can read.
"""

from __future__ import annotations

import unittest
from unittest import mock

from tradingagents.dataflows import earnings_utils
from tradingagents.dataflows.earnings_utils import (
    get_crypto_earnings_equivalent,
    get_earnings_calendar_data,
    get_earnings_surprises_analysis,
    get_finnhub_earnings_calendar,
)


def _earning(**overrides):
    row = {
        "date": "2026-02-25",
        "epsEstimate": 1.00,
        "epsActual": 1.20,
        "hour": "amc",
        "quarter": 4,
        "year": 2025,
        "revenueEstimate": 1_000_000.0,
        "revenueActual": 1_100_000.0,
    }
    row.update(overrides)
    return row


class Client:
    def __init__(self, payload=None, error=None):
        self.payload = payload if payload is not None else {"earningsCalendar": []}
        self.error = error
        self.calls = []

    def earnings_calendar(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.payload


class FinnhubCalendarTests(unittest.TestCase):
    def _fetch(self, client, ticker="NVDA"):
        with mock.patch(
            "tradingagents.dataflows.finnhub_utils.get_finnhub_client",
            lambda: client,
        ):
            return get_finnhub_earnings_calendar(ticker, "2026-01-01", "2026-03-31")

    def test_the_window_and_symbol_are_passed_to_the_vendor(self):
        client = Client()

        self._fetch(client)

        self.assertEqual(
            client.calls[0],
            {"_from": "2026-01-01", "to": "2026-03-31", "symbol": "NVDA"},
        )

    def test_a_reported_quarter_is_rendered(self):
        rendered = self._fetch(Client({"earningsCalendar": [_earning()]}))

        self.assertIn("## NVDA Earnings Calendar", rendered)
        self.assertIn("### 2026-02-25 - Q4 2025 (amc)", rendered)
        self.assertIn("**EPS Actual**: 1.2", rendered)

    def test_the_eps_surprise_is_computed(self):
        rendered = self._fetch(Client({"earningsCalendar": [_earning()]}))

        self.assertIn("**EPS Surprise**: 0.2000 (20.00%)", rendered)

    def test_the_revenue_surprise_is_computed(self):
        rendered = self._fetch(Client({"earningsCalendar": [_earning()]}))

        self.assertIn("**Revenue Surprise**: 100000 (10.00%)", rendered)

    def test_an_unreported_quarter_has_no_surprise(self):
        rendered = self._fetch(
            Client({"earningsCalendar": [_earning(epsActual=None, revenueActual=None)]})
        )

        self.assertIn("**EPS Surprise**: N/A", rendered)
        self.assertIn("**Revenue Surprise**: N/A", rendered)

    def test_a_zero_estimate_does_not_divide_by_zero(self):
        rendered = self._fetch(
            Client({"earningsCalendar": [_earning(epsEstimate=0.0, revenueEstimate=0.0)]})
        )

        self.assertIn("(0.00%)", rendered)

    def test_a_non_numeric_figure_reads_as_unavailable(self):
        rendered = self._fetch(
            Client({"earningsCalendar": [_earning(epsActual="beat", epsEstimate="n/a")]})
        )

        self.assertIn("**EPS Surprise**: N/A", rendered)

    def test_several_quarters_are_all_rendered(self):
        rendered = self._fetch(
            Client(
                {
                    "earningsCalendar": [
                        _earning(date="2026-02-25"),
                        _earning(date="2025-11-20"),
                    ]
                }
            )
        )

        self.assertIn("2026-02-25", rendered)
        self.assertIn("2025-11-20", rendered)

    def test_an_empty_calendar_says_so(self):
        rendered = self._fetch(Client({"earningsCalendar": []}))

        self.assertIn("No earnings data found for NVDA", rendered)

    def test_a_vendor_failure_is_reported_as_prose(self):
        rendered = self._fetch(Client(error=RuntimeError("quota exceeded")))

        self.assertIn("Error fetching earnings data for NVDA", rendered)
        self.assertIn("quota exceeded", rendered)


class CryptoEventsTests(unittest.TestCase):
    def test_the_briefing_says_crypto_has_no_earnings(self):
        rendered = get_crypto_earnings_equivalent("BTC/USD", "2026-01-01", "2026-03-31")

        self.assertIn("don't have traditional earnings", rendered)

    def test_the_quote_currency_is_stripped_from_the_heading(self):
        for ticker in ("BTC/USD", "BTCUSD", "BTCUSDT", "btc"):
            rendered = get_crypto_earnings_equivalent(ticker, "2026-01-01", "2026-03-31")

            self.assertIn("## BTC Major Events Calendar", rendered)

    def test_a_known_chain_gets_its_own_event_categories(self):
        rendered = get_crypto_earnings_equivalent("ETH/USD", "2026-01-01", "2026-03-31")

        self.assertIn("Key Event Categories for ETH", rendered)
        self.assertIn("Ethereum network upgrades", rendered)

    def test_an_unlisted_token_still_gets_the_monitoring_advice(self):
        rendered = get_crypto_earnings_equivalent("XYZ/USD", "2026-01-01", "2026-03-31")

        self.assertNotIn("Key Event Categories", rendered)
        self.assertIn("Recommendation", rendered)
        self.assertIn("CoinGecko", rendered)


class RoutingTests(unittest.TestCase):
    def test_a_stock_routes_to_the_earnings_calendar(self):
        with mock.patch.object(
            earnings_utils, "get_finnhub_earnings_calendar", lambda *a: "stock answer"
        ):
            self.assertEqual(
                get_earnings_calendar_data("AAPL", "2026-01-01", "2026-03-31"),
                "stock answer",
            )

    def test_a_crypto_pair_routes_to_the_events_briefing(self):
        with mock.patch.object(
            earnings_utils, "get_crypto_earnings_equivalent", lambda *a: "crypto answer"
        ):
            for ticker in ("BTC/USD", "ETH", "SOLUSD"):
                self.assertEqual(
                    get_earnings_calendar_data(ticker, "2026-01-01", "2026-03-31"),
                    "crypto answer",
                    ticker,
                )


class SurpriseAnalysisTests(unittest.TestCase):
    def test_the_lookback_window_is_derived_from_the_quarter_count(self):
        captured = {}

        def calendar(ticker, start, end):
            captured["start"] = start
            captured["end"] = end
            return "### 2026-02-25 - Q4 2025 (amc)"

        with mock.patch.object(
            earnings_utils, "get_finnhub_earnings_calendar", calendar
        ):
            get_earnings_surprises_analysis("NVDA", "2026-09-09", lookback_quarters=4)

        self.assertEqual(captured["end"], "2026-09-09")
        self.assertEqual(captured["start"], "2025-09-14")

    def test_the_analysis_wraps_the_raw_calendar(self):
        with mock.patch.object(
            earnings_utils,
            "get_finnhub_earnings_calendar",
            lambda *a: "### 2026-02-25 - Q4 2025 (amc)",
        ):
            rendered = get_earnings_surprises_analysis("NVDA", "2026-09-09")

        self.assertIn("## NVDA Earnings Surprise Analysis", rendered)
        self.assertIn("Key Patterns to Monitor", rendered)
        self.assertIn("Trading Implications", rendered)
        self.assertIn("### 2026-02-25 - Q4 2025 (amc)", rendered)

    def test_an_empty_calendar_is_passed_through_unwrapped(self):
        """There is nothing to analyse, so the framing would be misleading."""
        with mock.patch.object(
            earnings_utils,
            "get_finnhub_earnings_calendar",
            lambda *a: "No earnings data found for NVDA",
        ):
            rendered = get_earnings_surprises_analysis("NVDA", "2026-09-09")

        self.assertEqual(rendered, "No earnings data found for NVDA")

    def test_a_vendor_failure_is_passed_through(self):
        with mock.patch.object(
            earnings_utils,
            "get_finnhub_earnings_calendar",
            lambda *a: "Error fetching earnings data for NVDA: quota",
        ):
            rendered = get_earnings_surprises_analysis("NVDA", "2026-09-09")

        self.assertIn("Error fetching earnings data", rendered)

    def test_a_malformed_date_is_reported_rather_than_raised(self):
        rendered = get_earnings_surprises_analysis("NVDA", "not-a-date")

        self.assertIn("Error", rendered)


class ApiKeyTests(unittest.TestCase):
    def test_the_configured_key_wins(self):
        with mock.patch.object(earnings_utils, "get_api_key", lambda *a: "from-config"):
            self.assertEqual(
                earnings_utils.get_earnings_calendar_api_key(), "from-config"
            )

    def test_the_environment_is_the_fallback(self):
        with mock.patch.object(earnings_utils, "get_api_key", lambda *a: None):
            with mock.patch.dict(
                earnings_utils.os.environ,
                {"EARNINGS_CALENDAR_API_KEY": "from-env"},
                clear=False,
            ):
                self.assertEqual(
                    earnings_utils.get_earnings_calendar_api_key(), "from-env"
                )


if __name__ == "__main__":
    unittest.main()
