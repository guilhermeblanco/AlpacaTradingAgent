"""Tests for the live-web-search date gate.

Every dated source bounds itself — Finnhub takes from/to, SimFin filters on
publish date, Google News takes after:/before:, Reddit filters its window.
The hosted web-search tool takes none of that: the range only reaches it as
prose, which is a request rather than a constraint.

That is harmless for an analysis of today and serious for an analysis of a
past date, so live search is allowed only when the date is current. This is
the gate that decides.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

from tradingagents.agents.utils.agent_utils import Toolkit
from tradingagents.dataflows import interface
from tradingagents.dataflows.search_window import (
    FORCE_CONFIG_KEY,
    MARKET_TIMEZONE,
    bounded_reason,
    is_historical,
    live_search_allowed,
    market_today,
    parse_analysis_date,
)

#: Mid-afternoon in New York, so the market date is unambiguous.
NOW = datetime(2026, 9, 9, 15, 0, tzinfo=MARKET_TIMEZONE)


class MarketDateTests(unittest.TestCase):
    def test_the_market_date_is_eastern_not_utc(self):
        """Trade dates are chosen against the US calendar, so today is too."""
        just_after_midnight_utc = datetime(2026, 9, 10, 1, 0, tzinfo=timezone.utc)

        self.assertEqual(
            market_today(just_after_midnight_utc), date(2026, 9, 9)
        )

    def test_an_aware_time_is_converted(self):
        self.assertEqual(market_today(NOW), date(2026, 9, 9))

    def test_a_naive_time_is_taken_as_given(self):
        self.assertEqual(
            market_today(datetime(2026, 9, 9, 15, 0)), date(2026, 9, 9)
        )


class DateParsingTests(unittest.TestCase):
    def test_an_iso_date_is_read(self):
        self.assertEqual(parse_analysis_date("2026-09-09"), date(2026, 9, 9))

    def test_surrounding_whitespace_is_ignored(self):
        self.assertEqual(parse_analysis_date("  2026-09-09 "), date(2026, 9, 9))

    def test_a_date_or_datetime_is_accepted(self):
        self.assertEqual(parse_analysis_date(date(2026, 9, 9)), date(2026, 9, 9))
        self.assertEqual(parse_analysis_date(NOW), date(2026, 9, 9))

    def test_anything_unreadable_yields_nothing(self):
        for value in ("whenever", "", None, 12345):
            self.assertIsNone(parse_analysis_date(value), value)


class HistoricalTests(unittest.TestCase):
    def test_today_is_not_historical(self):
        self.assertFalse(is_historical("2026-09-09", now=NOW))

    def test_a_past_date_is_historical(self):
        self.assertTrue(is_historical("2026-01-15", now=NOW))

    def test_yesterday_is_historical(self):
        self.assertTrue(is_historical("2026-09-08", now=NOW))

    def test_a_future_date_is_not_treated_as_historical(self):
        """Nothing published after it exists yet, so there is nothing to leak."""
        self.assertFalse(is_historical("2026-12-01", now=NOW))

    def test_an_unreadable_date_is_treated_as_historical(self):
        """Refusing to guess is the safe direction: a bounded answer costs
        recall, an unbounded one costs correctness."""
        self.assertTrue(is_historical("whenever", now=NOW))
        self.assertTrue(is_historical(None, now=NOW))


class LiveSearchTests(unittest.TestCase):
    def _allowed(self, curr_date, config=None):
        return live_search_allowed(curr_date, config=config or {}, now=NOW)

    def test_a_current_date_may_search_live(self):
        self.assertTrue(self._allowed("2026-09-09"))

    def test_a_past_date_may_not(self):
        self.assertFalse(self._allowed("2026-01-15"))

    def test_point_in_time_sourcing_can_be_required_outright(self):
        self.assertFalse(self._allowed("2026-09-09", {FORCE_CONFIG_KEY: True}))

    def test_the_requirement_is_read_from_a_string_too(self):
        for value in ("true", "1", "yes", "on"):
            self.assertFalse(self._allowed("2026-09-09", {FORCE_CONFIG_KEY: value}))
        for value in ("false", "0", "no", ""):
            self.assertTrue(self._allowed("2026-09-09", {FORCE_CONFIG_KEY: value}))

    def test_the_process_configuration_is_consulted_when_none_is_given(self):
        with mock.patch(
            "tradingagents.dataflows.config.get_config",
            lambda: {FORCE_CONFIG_KEY: True},
        ):
            self.assertFalse(live_search_allowed("2026-09-09", now=NOW))

    def test_an_unreadable_configuration_does_not_block_a_live_run(self):
        with mock.patch(
            "tradingagents.dataflows.config.get_config",
            side_effect=RuntimeError("config unreadable"),
        ):
            self.assertTrue(live_search_allowed("2026-09-09", now=NOW))


class ReasonTests(unittest.TestCase):
    def test_a_current_date_needs_no_explanation(self):
        self.assertEqual(bounded_reason("2026-09-09", config={}, now=NOW), "")

    def test_a_past_date_says_why_it_was_bounded(self):
        reason = bounded_reason("2026-01-15", config={}, now=NOW)

        self.assertIn("past date", reason)
        self.assertIn("cannot be constrained", reason)

    def test_a_required_bound_names_the_setting(self):
        reason = bounded_reason("2026-09-09", config={FORCE_CONFIG_KEY: True}, now=NOW)

        self.assertIn(FORCE_CONFIG_KEY, reason)


class ToolStandDownTests(unittest.TestCase):
    """The three hosted-search tools compose from dated sources instead."""

    TOOLS = (
        ("get_stock_news_openai", ("NVDA", "2026-01-15")),
        ("get_global_news_openai", ("2026-01-15",)),
        ("get_fundamentals_openai", ("NVDA", "2026-01-15")),
    )

    CURRENT = (
        ("get_stock_news_openai", ("NVDA", "2026-09-09")),
        ("get_global_news_openai", ("2026-09-09",)),
        ("get_fundamentals_openai", ("NVDA", "2026-09-09")),
    )

    def setUp(self):
        for name, value in (
            ("get_google_news", "### Google headline\nbody"),
            ("get_finnhub_news", "### Finnhub headline\nbody"),
            ("get_finnhub_company_insider_sentiment", "### 2026-1:\nChange: -1"),
            ("get_finnhub_company_insider_transactions", "### Filing\nChange: -1"),
        ):
            patcher = mock.patch.object(
                interface, name, lambda *a, _v=value, **k: _v
            )
            patcher.start()
            self.addCleanup(patcher.stop)

        # A clock the gate will read as 2026-09-09 in New York.
        patcher = mock.patch(
            "tradingagents.dataflows.search_window.market_today",
            lambda now=None: date(2026, 9, 9),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _call(self, name, args, *, client=None):
        with mock.patch.object(interface, "get_api_key", lambda *a: "sk-test"):
            with mock.patch.object(interface, "get_config", lambda: {}):
                with mock.patch.object(
                    interface,
                    "get_openai_client_with_timeout",
                    lambda key, timeout_seconds=None: client or _Client(),
                ):
                    return getattr(interface, name)(*args)

    def test_a_historical_analysis_never_reaches_the_search_api(self):
        for name, args in self.TOOLS:
            client = _Client()

            self._call(name, args, client=client)

            self.assertEqual(client.calls, 0, name)

    def test_a_historical_analysis_says_why_it_was_bounded(self):
        for name, args in self.TOOLS:
            answer = self._call(name, args)

            self.assertIn("past date", answer, name)

    def test_a_historical_analysis_still_returns_the_dated_evidence(self):
        answer = self._call(*self.TOOLS[0])

        self.assertIn("Google headline", answer)
        self.assertIn("Finnhub headline", answer)

    def test_a_current_analysis_still_searches(self):
        for name, args in self.CURRENT:
            client = _Client()

            self._call(name, args, client=client)

            self.assertEqual(client.calls, 1, name)

    def test_requiring_point_in_time_sourcing_stands_the_search_down_today(self):
        with mock.patch(
            "tradingagents.dataflows.config.get_config",
            lambda: {FORCE_CONFIG_KEY: True},
        ):
            for name, args in self.CURRENT:
                client = _Client()

                answer = self._call(name, args, client=client)

                self.assertEqual(client.calls, 0, name)
                self.assertIn(FORCE_CONFIG_KEY, answer, name)

    def test_a_historical_analysis_needs_no_api_key(self):
        """Nothing is called, so a missing key is not a reason to fail."""
        with mock.patch.object(interface, "get_api_key", lambda *a: None):
            answer = interface.get_stock_news_openai("NVDA", "2026-01-15")

        self.assertNotIn("API key not found", answer)
        self.assertIn("past date", answer)

    def test_the_macro_tool_inherits_the_gate(self):
        """It delegates to the global one, so it is bounded too."""
        client = _Client()
        with mock.patch.object(interface, "get_api_key", lambda *a: "sk-test"):
            with mock.patch.object(interface, "get_config", lambda: {}):
                with mock.patch.object(
                    interface,
                    "get_openai_client_with_timeout",
                    lambda key, timeout_seconds=None: client,
                ):
                    answer = Toolkit.get_macro_news_openai.invoke(
                        {"curr_date": "2026-01-15"}
                    )

        self.assertEqual(client.calls, 0)
        self.assertIn("past date", answer)


class _Client:
    """Counts whether the hosted search was reached at all."""

    def __init__(self):
        self.calls = 0
        outer = self

        class Responses:
            def create(self, **params):
                from types import SimpleNamespace

                outer.calls += 1
                return SimpleNamespace(output_text="live answer", output=[], usage=None)

        self.responses = Responses()


if __name__ == "__main__":
    unittest.main()
