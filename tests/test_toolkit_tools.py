"""Tests for the Toolkit's tool surface and its availability probes.

Every tool an analyst can call is a thin `@tool`-decorated forwarder onto a
dataflow function, wrapped in the timing/telemetry decorator. What matters
is that each one reaches the function it claims to and passes the arguments
through unmangled — a tool wired to the wrong source produces a plausible
report about the wrong thing.

The `has_*` probes decide which tools get offered at all, so a probe that
reports a source as available when it isn't costs the analyst a whole
category of evidence.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tradingagents.agents.utils import agent_utils
from tradingagents.agents.utils.agent_utils import Toolkit, create_msg_delete


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "the report"


# (tool name, interface function it forwards to, arguments to invoke with)
FORWARDERS = (
    ("get_reddit_news", "get_reddit_global_news", {"curr_date": "2026-09-09"}),
    (
        "get_finnhub_news_recent",
        "get_finnhub_news",
        {"ticker": "NVDA", "curr_date": "2026-09-09", "look_back_days": 5},
    ),
    (
        "get_finnhub_news",
        "get_finnhub_news",
        {"ticker": "NVDA", "start_date": "2026-09-01", "end_date": "2026-09-09"},
    ),
    (
        "get_reddit_stock_info",
        "get_reddit_company_news",
        {"ticker": "NVDA", "curr_date": "2026-09-09"},
    ),
    (
        "get_market_data",
        "get_market_data",
        {"symbol": "NVDA", "start_date": "2026-09-01", "end_date": "2026-09-09"},
    ),
    (
        "get_finnhub_company_insider_sentiment",
        "get_finnhub_company_insider_sentiment",
        {"ticker": "NVDA", "curr_date": "2026-09-09"},
    ),
    (
        "get_finnhub_company_insider_transactions",
        "get_finnhub_company_insider_transactions",
        {"ticker": "NVDA", "curr_date": "2026-09-09"},
    ),
    (
        "get_simfin_balance_sheet",
        "get_simfin_balance_sheet",
        {"ticker": "NVDA", "freq": "annual", "curr_date": "2026-09-09"},
    ),
    (
        "get_simfin_cashflow",
        "get_simfin_cashflow",
        {"ticker": "NVDA", "freq": "annual", "curr_date": "2026-09-09"},
    ),
    (
        "get_simfin_income_stmt",
        "get_simfin_income_statements",
        {"ticker": "NVDA", "freq": "annual", "curr_date": "2026-09-09"},
    ),
    ("get_coindesk_news", "get_coindesk_news", {"ticker": "BTC/USD"}),
    (
        "get_google_news",
        "get_google_news",
        {"query": "NVDA", "curr_date": "2026-09-09"},
    ),
    (
        "get_stock_news_openai",
        "get_stock_news_openai",
        {"ticker": "NVDA", "curr_date": "2026-09-09"},
    ),
    ("get_global_news_openai", "get_global_news_openai", {"curr_date": "2026-09-09"}),
    (
        "get_fundamentals_openai",
        "get_fundamentals_openai",
        {"ticker": "NVDA", "curr_date": "2026-09-09"},
    ),
    (
        "get_earnings_calendar",
        "get_earnings_calendar",
        {"ticker": "NVDA", "start_date": "2026-01-01", "end_date": "2026-03-31"},
    ),
    (
        "get_earnings_surprise_analysis",
        "get_earnings_surprise_analysis",
        {"ticker": "NVDA", "curr_date": "2026-09-09"},
    ),
    ("get_macro_analysis", "get_macro_analysis", {"curr_date": "2026-09-09"}),
    (
        "get_economic_indicators",
        "get_economic_indicators",
        {"curr_date": "2026-09-09"},
    ),
    (
        "get_yield_curve_analysis",
        "get_yield_curve_analysis",
        {"curr_date": "2026-09-09"},
    ),
    (
        "get_defillama_fundamentals",
        "get_defillama_fundamentals",
        {"ticker": "UNI"},
    ),
    (
        "get_market_data_report",
        "get_market_data_window",
        {"symbol": "NVDA", "curr_date": "2026-09-09", "look_back_days": 60},
    ),
    (
        "get_technical_brief",
        "get_technical_brief",
        {"symbol": "NVDA", "curr_date": "2026-09-09"},
    ),
    (
        "get_stockstats_indicators_report",
        "get_stock_stats_indicators_window",
        {"symbol": "NVDA", "indicator": "rsi_14", "curr_date": "2026-09-09"},
    ),
    (
        "get_stockstats_indicators_report_online",
        "get_stockstats_indicator_history",
        {"symbol": "NVDA", "indicator": "rsi_14", "curr_date": "2026-09-09"},
    ),
)


class ForwardingTests(unittest.TestCase):
    def setUp(self):
        # The wrapper retries a tool whose output looks thin; that behaviour
        # has its own tests, and here it would double every forward.
        original = dict(Toolkit._config)
        self.addCleanup(lambda: Toolkit._config.clear() or Toolkit._config.update(original))
        Toolkit._config["tool_semantic_retry_enabled"] = False

    def _invoke(self, tool_name, target, args):
        recorder = Recorder()
        with mock.patch.object(agent_utils.interface, target, recorder):
            answer = getattr(Toolkit, tool_name).invoke(dict(args))
        return answer, recorder

    def test_every_tool_reaches_its_dataflow_function(self):
        for tool_name, target, args in FORWARDERS:
            _answer, recorder = self._invoke(tool_name, target, args)

            self.assertEqual(len(recorder.calls), 1, tool_name)

    def test_every_tool_returns_what_its_source_produced(self):
        for tool_name, target, args in FORWARDERS:
            answer, _recorder = self._invoke(tool_name, target, args)

            self.assertIn("the report", str(answer), tool_name)

    def test_the_symbol_reaches_the_dataflow_function(self):
        for tool_name, target, args in FORWARDERS:
            symbol = args.get("ticker") or args.get("symbol") or args.get("query")
            if not symbol:
                continue

            _answer, recorder = self._invoke(tool_name, target, args)
            call_args, call_kwargs = recorder.calls[0]

            self.assertIn(
                symbol, list(call_args) + list(call_kwargs.values()), tool_name
            )

    def test_the_lookback_is_forwarded_when_given(self):
        _answer, recorder = self._invoke(
            "get_finnhub_news_recent",
            "get_finnhub_news",
            {"ticker": "NVDA", "curr_date": "2026-09-09", "look_back_days": 5},
        )

        self.assertIn(5, recorder.calls[0][0])

    def test_the_offline_indicator_tool_asks_for_the_cached_window(self):
        """The False argument is what keeps a backtest off the network."""
        _answer, recorder = self._invoke(
            "get_stockstats_indicators_report",
            "get_stock_stats_indicators_window",
            {"symbol": "NVDA", "indicator": "rsi_14", "curr_date": "2026-09-09"},
        )

        self.assertIn(False, recorder.calls[0][0])

    def test_the_online_indicator_tool_forwards_its_timeframe_and_budget(self):
        _answer, recorder = self._invoke(
            "get_stockstats_indicators_report_online",
            "get_stockstats_indicator_history",
            {
                "symbol": "NVDA",
                "indicator": "macd",
                "curr_date": "2026-09-09",
                "timeframe": "4Hour",
                "max_points": 12,
            },
        )

        _args, kwargs = recorder.calls[0]
        self.assertEqual(kwargs["timeframe"], "4Hour")
        self.assertEqual(kwargs["max_points"], 12)


class StockDataTableTests(unittest.TestCase):
    RAW = (
        "Stock data for NVDA from 2025-06-01 to 2025-09-01\n"
        "timestamp,open,close\n"
        "2025-07-08 04:00:00+00:00,100,101\n"
    )

    def _table(self, raw):
        with mock.patch.object(
            agent_utils.interface, "get_market_data_window", lambda *a, **k: raw
        ):
            return Toolkit.get_stock_data_table.invoke(
                {"symbol": "NVDA", "curr_date": "2026-09-09"}
            )

    def test_the_timestamp_column_is_renamed(self):
        self.assertIn("Date", self._table(self.RAW))

    def test_the_time_of_day_is_dropped_from_each_row(self):
        rendered = self._table(self.RAW)

        self.assertIn("2025-07-08,", rendered)
        self.assertNotIn("04:00:00", rendered)

    def test_the_lookback_is_stated_in_the_title(self):
        self.assertIn("day lookback", self._table(self.RAW))

    def test_an_unparseable_payload_is_returned_unchanged(self):
        self.assertEqual(self._table(""), "")


class AvailabilityProbeTests(unittest.TestCase):
    def _keys(self, **present):
        def get_api_key(config_key, env_key=None):
            return "value" if present.get(config_key) else ""

        return mock.patch.object(agent_utils, "get_api_key", get_api_key)

    def test_each_probe_reads_its_own_key(self):
        cases = (
            ("has_openai_web_search", "openai_api_key"),
            ("has_finnhub", "finnhub_api_key"),
            ("has_fred", "fred_api_key"),
            ("has_coindesk", "coindesk_api_key"),
        )
        toolkit = Toolkit()

        for probe, key in cases:
            with self._keys(**{key: True}):
                self.assertTrue(getattr(toolkit, probe)(), probe)
            with self._keys():
                self.assertFalse(getattr(toolkit, probe)(), probe)

    def test_alpaca_needs_both_halves_of_the_credential(self):
        toolkit = Toolkit()

        with self._keys(alpaca_api_key=True, alpaca_secret_key=True):
            self.assertTrue(toolkit.has_alpaca_credentials())
        with self._keys(alpaca_api_key=True):
            self.assertFalse(toolkit.has_alpaca_credentials())
        with self._keys(alpaca_secret_key=True):
            self.assertFalse(toolkit.has_alpaca_credentials())

    def test_a_failing_key_lookup_reads_as_unavailable(self):
        with mock.patch.object(
            agent_utils, "get_api_key", mock.Mock(side_effect=RuntimeError("vault down"))
        ):
            self.assertFalse(Toolkit().has_finnhub())


class SimfinAvailabilityTests(unittest.TestCase):
    FILES = (
        ("balance_sheets", "us-balance-{freq}.csv"),
        ("cashflow", "us-cashflow-{freq}.csv"),
        ("income_statements", "us-income-{freq}.csv"),
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)

    def _toolkit(self, data_dir=None):
        toolkit = Toolkit()
        patcher = mock.patch.object(
            type(toolkit),
            "config",
            property(lambda _self: {"data_dir": data_dir if data_dir is not None else str(self.data_dir)}),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return toolkit

    def _write(self, folder, pattern, freq):
        path = self.data_dir / "simfin_data_all" / folder / "companies" / "us"
        path.mkdir(parents=True, exist_ok=True)
        (path / pattern.format(freq=freq)).write_text("x", encoding="utf-8")

    def test_a_complete_frequency_set_reads_as_available(self):
        for folder, pattern in self.FILES:
            self._write(folder, pattern, "annual")

        self.assertTrue(self._toolkit().has_simfin_data())

    def test_either_frequency_is_enough(self):
        for folder, pattern in self.FILES:
            self._write(folder, pattern, "quarterly")

        self.assertTrue(self._toolkit().has_simfin_data())

    def test_a_partial_set_does_not_count(self):
        """Offering the statements when one is missing wastes a tool call."""
        self._write(*self.FILES[0], "annual")

        self.assertFalse(self._toolkit().has_simfin_data())

    def test_no_files_at_all_reads_as_unavailable(self):
        self.assertFalse(self._toolkit().has_simfin_data())

    def test_no_configured_data_directory_reads_as_unavailable(self):
        self.assertFalse(self._toolkit(data_dir="").has_simfin_data())


class MarketDataProbeTests(unittest.TestCase):
    def _provider(self, name="yfinance", supports=True):
        return mock.Mock(name=name, **{"supports.return_value": supports})

    def test_a_supporting_provider_reads_as_available(self):
        provider = mock.Mock()
        provider.name = "yfinance"
        provider.supports.return_value = True

        with mock.patch(
            "tradingagents.marketdata.get_research_market_data_provider",
            lambda *a: provider,
        ):
            self.assertTrue(Toolkit().has_research_market_data("NVDA"))

    def test_a_provider_that_does_not_cover_the_symbol_reads_as_unavailable(self):
        provider = mock.Mock()
        provider.name = "yfinance"
        provider.supports.return_value = False

        with mock.patch(
            "tradingagents.marketdata.get_research_market_data_provider",
            lambda *a: provider,
        ):
            self.assertFalse(Toolkit().has_research_market_data("NVDA"))

    def test_alpaca_without_credentials_reads_as_unavailable(self):
        """Otherwise every market tool call fails on authentication."""
        provider = mock.Mock()
        provider.name = "alpaca"
        provider.supports.return_value = True

        with mock.patch(
            "tradingagents.marketdata.get_research_market_data_provider",
            lambda *a: provider,
        ):
            with mock.patch.object(agent_utils, "get_api_key", lambda *a: ""):
                self.assertFalse(Toolkit().has_research_market_data("NVDA"))

    def test_a_failing_provider_lookup_reads_as_unavailable(self):
        with mock.patch(
            "tradingagents.marketdata.get_research_market_data_provider",
            mock.Mock(side_effect=RuntimeError("no provider")),
        ):
            self.assertFalse(Toolkit().has_research_market_data("NVDA"))


class MessageClearingTests(unittest.TestCase):
    def test_every_message_is_marked_for_removal(self):
        from langchain_core.messages import AIMessage, RemoveMessage

        state = {"messages": [AIMessage(content="a", id="1"), AIMessage(content="b", id="2")]}

        removals = create_msg_delete()(state)["messages"]

        self.assertEqual([m.id for m in removals], ["1", "2"])
        self.assertTrue(all(isinstance(m, RemoveMessage) for m in removals))

    def test_an_empty_history_needs_no_removals(self):
        self.assertEqual(create_msg_delete()({"messages": []})["messages"], [])


class ConfigTests(unittest.TestCase):
    def test_construction_merges_the_given_config(self):
        original = dict(Toolkit._config)
        self.addCleanup(lambda: Toolkit._config.update(original))

        Toolkit({"online_tools": False})

        self.assertFalse(Toolkit().config["online_tools"])


if __name__ == "__main__":
    unittest.main()
