import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph


class FakeLLM:
    def invoke(self, _prompt):
        raise AssertionError("Mocked graph should not call an LLM")


class FakeClient:
    def get_llm(self):
        return FakeLLM()


class FakeCompiledGraph:
    def __init__(self, final_state):
        self.final_state = final_state

    def invoke(self, _state, **_kwargs):
        return self.final_state


class FakeWorkflow:
    def __init__(self, final_state):
        self.final_state = final_state
        self.compile_calls = []

    def compile(self, checkpointer=None):
        self.compile_calls.append(checkpointer)
        return FakeCompiledGraph(self.final_state)


def _final_state(ticker, trade_date):
    return {
        "company_of_interest": ticker,
        "trade_date": trade_date,
        "market_report": "market",
        "sentiment_report": "sentiment",
        "news_report": "news",
        "fundamentals_report": "fundamentals",
        "macro_report": "macro",
        "report_context": {"stats": {"macro_report": 1}},
        "investment_debate_state": {
            "bull_history": "bull",
            "bear_history": "bear",
            "history": "debate",
            "current_response": "manager",
            "judge_decision": "research decision",
        },
        "trader_investment_plan": "trader plan\nFINAL TRANSACTION PROPOSAL: **BUY**",
        "risk_debate_state": {
            "risky_history": "risky",
            "safe_history": "safe",
            "neutral_history": "neutral",
            "history": "risk debate",
            "judge_decision": "risk decision",
        },
        "investment_plan": "investment plan",
        "final_trade_decision": "risk decision\nFINAL TRANSACTION PROPOSAL: **BUY**",
    }


class MockedTradingGraphTests(unittest.TestCase):
    def test_propagate_preserves_macro_and_safe_paths_with_checkpoint(self):
        # ignore_cleanup_errors: chromadb holds its store files open on
        # Windows, so temp-dir removal can race the process handle.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp_path = Path(tmp)
            for ticker, safe_ticker in (("AAPL", "AAPL"), ("BTC/USD", "BTC_USD")):
                with self.subTest(ticker=ticker):
                    config = DEFAULT_CONFIG.copy()
                    config.update(
                        {
                            "llm_provider": "local_openai",
                            "backend_url": "http://localhost:11434/v1",
                            "quick_think_llm": "gpt-4.1",
                            "deep_think_llm": "gpt-4.1",
                            "data_cache_dir": str(tmp_path / f"cache-{safe_ticker}"),
                            "results_dir": str(tmp_path / "results"),
                            "memory_log_path": str(tmp_path / f"memory-{safe_ticker}.md"),
                            "agent_memory_dir": str(tmp_path / f"agent-memory-{safe_ticker}"),
                            "checkpoint_enabled": True,
                        }
                    )
                    final_state = _final_state(ticker, "2026-01-02")
                    workflow = FakeWorkflow(final_state)

                    with patch("tradingagents.graph.trading_graph.create_llm_client", return_value=FakeClient()), patch(
                        "tradingagents.graph.trading_graph.GraphSetup.setup_graph",
                        return_value=workflow,
                    ):
                        graph = TradingAgentsGraph(
                            selected_analysts=["market", "news", "macro"],
                            config=config,
                            debug=False,
                        )
                        state, signal = graph.propagate(ticker, "2026-01-02")

                    self.assertEqual(signal, "BUY")
                    self.assertEqual(state["macro_report"], "macro")
                    self.assertTrue(
                        (tmp_path / "results" / safe_ticker / "TradingAgentsStrategy_logs" / "full_states_log.json").exists()
                    )
                    self.assertIn(ticker, Path(config["memory_log_path"]).read_text(encoding="utf-8"))
                    self.assertGreaterEqual(len(workflow.compile_calls), 2)

    def test_provider_specific_runtime_options_reach_llm_factory(self):
        cases = [
            ("google", {"google_thinking_level": "high"}, "thinking_level", "high"),
            ("anthropic", {"anthropic_effort": "medium"}, "effort", "medium"),
            ("xai", {"xai_reasoning_effort": "xhigh"}, "reasoning_effort", "xhigh"),
        ]

        for provider, provider_config, expected_key, expected_value in cases:
            with self.subTest(provider=provider), tempfile.TemporaryDirectory(
                ignore_cleanup_errors=True
            ) as tmp:
                calls = []

                def fake_create_llm_client(**kwargs):
                    calls.append(kwargs)
                    return FakeClient()

                config = DEFAULT_CONFIG.copy()
                config.update(
                    {
                        "llm_provider": provider,
                        "quick_think_llm": "custom-quick",
                        "deep_think_llm": "custom-deep",
                        "data_cache_dir": str(Path(tmp) / "cache"),
                        "results_dir": str(Path(tmp) / "results"),
                        "memory_log_path": str(Path(tmp) / "memory.md"),
                        "agent_memory_dir": str(Path(tmp) / "agent-memory"),
                        **provider_config,
                    }
                )
                workflow = FakeWorkflow(_final_state("AAPL", "2026-01-02"))

                with patch("tradingagents.graph.trading_graph.create_llm_client", side_effect=fake_create_llm_client), patch(
                    "tradingagents.graph.trading_graph.GraphSetup.setup_graph",
                    return_value=workflow,
                ):
                    TradingAgentsGraph(
                        selected_analysts=["market", "macro"],
                        config=config,
                        debug=False,
                    )

                self.assertEqual(len(calls), 2)
                self.assertTrue(all(call.get(expected_key) == expected_value for call in calls))


if __name__ == "__main__":
    unittest.main()


class SymbolConversionTests(unittest.TestCase):
    """Reflection fetches outcome prices from Yahoo, which spells pairs with
    a hyphen."""

    def _graph(self, tmp_path, **overrides):
        config = DEFAULT_CONFIG.copy()
        config.update(
            {
                "llm_provider": "local_openai",
                "backend_url": "http://localhost:11434/v1",
                "data_cache_dir": str(tmp_path / "cache"),
                "results_dir": str(tmp_path / "results"),
                "memory_log_path": str(tmp_path / "memory.md"),
                "agent_memory_dir": str(tmp_path / "agent-memory"),
            }
        )
        config.update(overrides)
        with patch(
            "tradingagents.graph.trading_graph.create_llm_client",
            return_value=FakeClient(),
        ), patch(
            "tradingagents.graph.trading_graph.GraphSetup.setup_graph",
            return_value=FakeWorkflow({}),
        ):
            return TradingAgentsGraph(
                selected_analysts=["market"], config=config, debug=False
            )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.graph = self._graph(Path(self._tmp.name))

    def test_a_pair_is_converted_to_yahoo_spelling(self):
        self.assertEqual(self.graph._ticker_for_yfinance("BTC/USD"), "BTC-USD")

    def test_an_equity_passes_through(self):
        self.assertEqual(self.graph._ticker_for_yfinance("NVDA"), "NVDA")

    def test_an_unconvertible_symbol_falls_back_to_a_hyphen(self):
        with patch(
            "tradingagents.graph.trading_graph.TickerUtils.convert_for_api",
            side_effect=RuntimeError("cannot normalize"),
        ):
            self.assertEqual(self.graph._ticker_for_yfinance("BTC/USD"), "BTC-USD")

    def test_an_equity_is_measured_against_the_index(self):
        self.assertEqual(self.graph._benchmark_for("NVDA"), "SPY")

    def test_the_index_is_not_measured_against_itself(self):
        self.assertIsNone(self.graph._benchmark_for("SPY"))

    def test_a_token_is_measured_against_bitcoin(self):
        self.assertEqual(self.graph._benchmark_for("ETH/USD"), "BTC-USD")

    def test_bitcoin_is_not_measured_against_itself(self):
        self.assertIsNone(self.graph._benchmark_for("BTC/USD"))


class OutcomeReturnTests(SymbolConversionTests):
    """The realized return is what turns a decision into a lesson."""

    def _download(self, frame, error=None):
        def download(symbol, **kwargs):
            if error:
                raise error
            return frame

        return patch("tradingagents.graph.trading_graph.yf.download", download)

    @staticmethod
    def _closes(values):
        import pandas as pd

        return pd.DataFrame(
            {"Close": values},
            index=pd.date_range("2026-01-02", periods=len(values), freq="D"),
        )

    def _fetch(self, frame, *, error=None, start=None, holding_days=5):
        from datetime import date

        with self._download(frame, error):
            return self.graph._fetch_return(
                "NVDA", start or date(2026, 1, 2), holding_days
            )

    def test_the_return_is_measured_across_the_window(self):
        self.assertAlmostEqual(self._fetch(self._closes([100.0, 110.0])), 0.10)

    def test_a_window_that_has_not_closed_yet_is_not_measured(self):
        from datetime import date, timedelta

        self.assertIsNone(
            self._fetch(self._closes([100.0, 110.0]), start=date.today())
        )
        self.assertIsNone(
            self._fetch(
                self._closes([100.0, 110.0]),
                start=date.today() - timedelta(days=1),
            )
        )

    def test_a_failed_download_yields_no_measurement(self):
        self.assertIsNone(
            self._fetch(None, error=RuntimeError("yahoo unreachable"))
        )

    def test_an_empty_response_yields_no_measurement(self):
        import pandas as pd

        self.assertIsNone(self._fetch(pd.DataFrame()))
        self.assertIsNone(self._fetch(None))

    def test_a_response_without_closes_yields_no_measurement(self):
        import pandas as pd

        self.assertIsNone(self._fetch(pd.DataFrame({"Open": [100.0, 110.0]})))

    def test_a_single_session_is_not_a_return(self):
        self.assertIsNone(self._fetch(self._closes([100.0])))

    def test_a_multiindex_response_takes_the_first_column(self):
        import pandas as pd

        frame = pd.concat({"NVDA": self._closes([100.0, 110.0])}, axis=1)
        frame.columns = pd.MultiIndex.from_tuples([("Close", "NVDA")])

        self.assertAlmostEqual(self._fetch(frame), 0.10)

    def test_a_zero_opening_price_is_not_divided_by(self):
        self.assertIsNone(self._fetch(self._closes([0.0, 110.0])))

    def test_missing_sessions_are_dropped_before_measuring(self):
        import numpy as np

        frame = self._closes([100.0, np.nan, 110.0])

        self.assertAlmostEqual(self._fetch(frame), 0.10)


class OutcomeResolutionTests(SymbolConversionTests):
    """Pending decisions are resolved once their holding window has closed."""

    def _resolve(self, entries, *, returns=None, reflect_error=None,
                 trade_date="2026-02-01"):
        recorded = []
        reflected = []

        self.graph.memory_log.get_pending_entries = lambda ticker: list(entries)
        self.graph.memory_log.update_with_outcome = lambda **kwargs: recorded.append(
            kwargs
        )
        self.graph._reflect_agents_on_outcome = lambda *args: reflected.append(args)

        def fetch(ticker, start, holding_days):
            return (returns or {}).get(ticker)

        self.graph._fetch_return = fetch
        if reflect_error:
            self.graph.reflector.reflect_on_final_decision = mock_raiser(reflect_error)
        else:
            self.graph.reflector.reflect_on_final_decision = (
                lambda decision, raw, alpha: "the lesson"
            )

        self.graph._resolve_memory_log_outcomes("NVDA", trade_date)
        return recorded, reflected

    ENTRY = {"date": "2026-01-02", "decision": "BUY"}

    def test_a_closed_decision_is_recorded_with_its_alpha(self):
        recorded, reflected = self._resolve(
            [self.ENTRY], returns={"NVDA": 0.10, "SPY": 0.04}
        )

        self.assertAlmostEqual(recorded[0]["raw_return"], 0.10)
        self.assertAlmostEqual(recorded[0]["alpha_return"], 0.06)
        self.assertEqual(recorded[0]["reflection"], "the lesson")
        self.assertEqual(len(reflected), 1)

    def test_without_a_benchmark_reading_there_is_no_alpha(self):
        recorded, _reflected = self._resolve([self.ENTRY], returns={"NVDA": 0.10})

        self.assertIsNone(recorded[0]["alpha_return"])

    def test_an_entry_from_today_is_not_yet_resolvable(self):
        recorded, _reflected = self._resolve(
            [{"date": "2026-02-01", "decision": "BUY"}], returns={"NVDA": 0.10}
        )

        self.assertEqual(recorded, [])

    def test_an_entry_with_no_date_is_skipped(self):
        recorded, _reflected = self._resolve(
            [{"decision": "BUY"}], returns={"NVDA": 0.10}
        )

        self.assertEqual(recorded, [])

    def test_an_entry_with_an_unreadable_date_is_skipped(self):
        recorded, _reflected = self._resolve(
            [{"date": "whenever", "decision": "BUY"}], returns={"NVDA": 0.10}
        )

        self.assertEqual(recorded, [])

    def test_a_decision_with_no_price_data_stays_pending(self):
        recorded, _reflected = self._resolve([self.ENTRY], returns={})

        self.assertEqual(recorded, [])

    def test_a_failed_reflection_still_records_the_outcome(self):
        """The number is the fact; the prose is commentary."""
        recorded, _reflected = self._resolve(
            [self.ENTRY],
            returns={"NVDA": 0.10},
            reflect_error=RuntimeError("model unavailable"),
        )

        self.assertAlmostEqual(recorded[0]["raw_return"], 0.10)
        self.assertIn("reflection generation failed", recorded[0]["reflection"])

    def test_an_unreadable_run_date_falls_back_to_today(self):
        recorded, _reflected = self._resolve(
            [self.ENTRY], returns={"NVDA": 0.10}, trade_date="whenever"
        )

        self.assertEqual(len(recorded), 1)


def mock_raiser(error):
    def raise_it(*_args, **_kwargs):
        raise error

    return raise_it
