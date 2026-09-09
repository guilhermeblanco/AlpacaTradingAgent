"""Tests for the Start/Stop button's decision logic.

This one callback decides whether a click starts or stops a run, which
scheduling mode is armed, what configuration the analysis thread is handed,
and what the operator is told. The thread itself is not started here — what
is under test is everything that happens before it, plus the refusals that
mean it never starts.
"""

from __future__ import annotations

import unittest
from unittest import mock

import dash

from conftest import dash_callback
from webui.callbacks import control_callbacks
from webui.utils.state import AppState

# In the order the callback declares them.
STATE_ORDER = (
    "tickers",
    "symbol_query_input",
    "analysts_market",
    "analysts_social",
    "analysts_news",
    "analysts_fundamentals",
    "analysts_macro",
    "research_depth",
    "llm_provider",
    "backend_url",
    "output_language",
    "checkpoint_enabled",
    "quick_llm",
    "deep_llm",
    "quick_llm_custom_model",
    "deep_llm_custom_model",
    "google_thinking_level",
    "anthropic_effort",
    "xai_reasoning_effort",
    "quick_reasoning_effort",
    "quick_verbosity",
    "quick_summary",
    "quick_temperature",
    "quick_top_p",
    "quick_max_output_tokens",
    "quick_store",
    "quick_parallel_tool_calls",
    "deep_reasoning_effort",
    "deep_verbosity",
    "deep_summary",
    "deep_temperature",
    "deep_top_p",
    "deep_max_output_tokens",
    "deep_store",
    "deep_parallel_tool_calls",
    "allow_shorts",
    "loop_enabled",
    "loop_interval",
    "trade_enabled",
    "trade_amount",
    "market_hour_enabled",
    "market_hours_input",
    "screener_enabled",
    "screener_interval",
)

DEFAULTS = {
    "tickers": "NVDA",
    "analysts_market": True,
    "analysts_social": True,
    "research_depth": "Medium",
    "llm_provider": "openai",
    "quick_llm": "gpt-5.4-nano",
    "deep_llm": "gpt-5.4",
    "loop_interval": 60,
    "trade_amount": 1000,
    "market_hours_input": "9,15",
    "screener_interval": 30,
}


class Thread:
    """Captures the analysis thread instead of running it."""

    started = []

    def __init__(self, target=None, **kwargs):
        self.target = target

    def start(self):
        Thread.started.append(self.target)


class ControlButtonFixture(unittest.TestCase):
    def setUp(self):
        app = dash.Dash(__name__, suppress_callback_exceptions=True)
        control_callbacks.register_control_callbacks(app)
        self.click = dash_callback(app, "result-text.children")

        self.state = AppState()
        patcher = mock.patch.object(control_callbacks, "app_state", self.state)
        patcher.start()
        self.addCleanup(patcher.stop)

        Thread.started = []
        threads = mock.patch.object(control_callbacks.threading, "Thread", Thread)
        threads.start()
        self.addCleanup(threads.stop)

    def _click(self, n_clicks=1, **overrides):
        values = dict(DEFAULTS)
        values.update(overrides)
        args = [values.get(name) for name in STATE_ORDER]
        return self.click(n_clicks, *args)

    @staticmethod
    def _message(result):
        return result[0]

    @staticmethod
    def _store(result):
        return result[1]


class IdleClickTests(ControlButtonFixture):
    def test_a_click_that_never_happened_changes_nothing(self):
        message, *_rest = self._click(n_clicks=None)

        self.assertIs(message, dash.no_update)

    def test_a_zero_click_count_changes_nothing(self):
        message, *_rest = self._click(n_clicks=0)

        self.assertIs(message, dash.no_update)


class StopTests(ControlButtonFixture):
    def test_a_click_during_a_run_stops_it(self):
        self.state.analysis_running = True

        message, *_rest = self._click()

        self.assertEqual(message, "Analysis stopped.")
        self.assertFalse(self.state.analysis_running)
        self.assertEqual(Thread.started, [])

    def test_a_click_during_screener_mode_stops_the_screener(self):
        self.state.screener_enabled = True

        message, *_rest = self._click()

        self.assertIn("Screener mode stopped", message)
        self.assertFalse(self.state.analysis_running)

    def test_a_click_during_loop_mode_stops_the_loop(self):
        self.state.loop_enabled = True

        message, *_rest = self._click()

        self.assertIn("Loop analysis stopped", message)

    def test_a_click_during_market_hour_mode_stops_the_schedule(self):
        self.state.market_hour_enabled = True

        message, *_rest = self._click()

        self.assertIn("Market hour analysis stopped", message)

    def test_screener_mode_takes_precedence_when_several_are_armed(self):
        self.state.screener_enabled = True
        self.state.loop_enabled = True

        message, *_rest = self._click()

        self.assertIn("Screener mode stopped", message)


class RefusalTests(ControlButtonFixture):
    def test_starting_with_no_symbols_is_refused(self):
        message, *_rest = self._click(tickers="")

        self.assertIn("at least one stock symbol", message)
        self.assertEqual(Thread.started, [])

    def test_an_unnamed_custom_quick_model_is_refused(self):
        message, *_rest = self._click(quick_llm="custom", quick_llm_custom_model="")

        self.assertIn("quick thinker", message)
        self.assertEqual(Thread.started, [])

    def test_an_unnamed_custom_deep_model_is_refused(self):
        message, *_rest = self._click(deep_llm="custom", deep_llm_custom_model="")

        self.assertIn("deep thinker", message)
        self.assertEqual(Thread.started, [])

    def test_a_named_custom_model_is_accepted(self):
        message, *_rest = self._click(
            quick_llm="custom", quick_llm_custom_model="some-org/some-model"
        )

        self.assertIn("Starting real-time analysis", message)

    def test_unparseable_market_hours_are_refused(self):
        message, *_rest = self._click(
            market_hour_enabled=True, market_hours_input="not-an-hour"
        )

        self.assertIn("Invalid market hours", message)
        self.assertEqual(Thread.started, [])


class StartTests(ControlButtonFixture):
    def test_a_single_run_starts_the_analysis_thread(self):
        message, store, *_rest = self._click()

        self.assertEqual(len(Thread.started), 1)
        self.assertTrue(self.state.analysis_running)
        self.assertIn("single run mode", message)
        self.assertTrue(store["analysis_started"])

    def test_the_symbols_are_parsed_from_the_input(self):
        _message, store, *_rest = self._click(tickers="nvda, aapl ,msft")

        self.assertEqual(store["symbols"], ["NVDA", "AAPL", "MSFT"])
        self.assertEqual(store["num_symbols"], 3)

    def test_the_search_box_stands_in_when_the_ticker_field_is_empty(self):
        _message, store, *_rest = self._click(
            tickers="", symbol_query_input="nvda; aapl"
        )

        self.assertEqual(store["symbols"], ["NVDA", "AAPL"])

    def test_each_symbol_gets_its_state_before_the_thread_starts(self):
        """Pagination reads these, and it renders before the run does."""
        self._click(tickers="NVDA,AAPL")

        self.assertEqual(sorted(self.state.symbol_states), ["AAPL", "NVDA"])

    def test_the_selected_analysts_are_recorded(self):
        self._click(
            analysts_market=True,
            analysts_social=False,
            analysts_news=True,
            analysts_fundamentals=False,
            analysts_macro=True,
        )

        self.assertEqual(
            self.state.active_analysts,
            ["Market Analyst", "News Analyst", "Macro Analyst"],
        )

    def test_the_pagination_bounds_match_the_symbol_count(self):
        _message, _store, chart_max, chart_page, report_max, report_page = self._click(
            tickers="NVDA,AAPL"
        )

        self.assertEqual((chart_max, report_max), (2, 2))
        self.assertEqual((chart_page, report_page), (1, 1))


class SchedulingModeTests(ControlButtonFixture):
    def test_loop_mode_reports_its_interval(self):
        message, store, *_rest = self._click(loop_enabled=True, loop_interval=15)

        self.assertIn("loop mode", message)
        self.assertIn("every 15 minutes", message)
        self.assertEqual(self.state.loop_interval_minutes, 15)

    def test_a_missing_loop_interval_falls_back_to_an_hour(self):
        self._click(loop_enabled=True, loop_interval=None)

        self.assertEqual(self.state.loop_interval_minutes, 60)

    def test_market_hour_mode_reports_its_schedule_in_wall_clock_terms(self):
        message, _store, *_rest = self._click(
            market_hour_enabled=True, market_hours_input="9,15"
        )

        self.assertIn("market hour mode", message)
        self.assertIn("9:00 AM", message)
        self.assertIn("3:00 PM", message)

    def test_noon_is_reported_as_noon(self):
        message, _store, *_rest = self._click(
            market_hour_enabled=True, market_hours_input="12"
        )

        self.assertIn("12:00 PM", message)

    def test_screener_mode_needs_no_symbols(self):
        message, store, *_rest = self._click(tickers="", screener_enabled=True)

        self.assertIn("Screener mode active", message)
        self.assertEqual(store["symbols"], ["SCREENER"])
        self.assertEqual(len(Thread.started), 1)

    def test_the_screener_interval_is_reported(self):
        message, _store, *_rest = self._click(
            tickers="", screener_enabled=True, screener_interval=45
        )

        self.assertIn("every 45 minutes", message)


class TradingConfigTests(ControlButtonFixture):
    def test_auto_trading_is_recorded_with_its_size(self):
        self._click(trade_enabled=True, trade_amount=2500)

        self.assertTrue(self.state.trade_enabled)
        self.assertEqual(self.state.trade_amount, 2500)

    def test_a_missing_size_falls_back_to_the_default(self):
        self._click(trade_enabled=True, trade_amount=None)

        self.assertEqual(self.state.trade_amount, 1000)

    def test_a_non_positive_size_falls_back_to_the_default(self):
        self._click(trade_enabled=True, trade_amount=0)

        self.assertEqual(self.state.trade_amount, 1000)


class RefreshRestoreTests(unittest.TestCase):
    """A page refresh loses the browser-side state but not the run."""

    def setUp(self):
        app = dash.Dash(__name__, suppress_callback_exceptions=True)
        control_callbacks.register_control_callbacks(app)
        registered = app.callback_map
        self.restore_pagination = next(
            spec["callback"].__wrapped__
            for key, spec in registered.items()
            if "chart-pagination.max_value" in key and "app-store" in str(spec["inputs"])
        )
        self.restore_status = next(
            spec["callback"].__wrapped__
            for key, spec in registered.items()
            if "result-text.children" in key and "app-store" in str(spec["inputs"])
        )
        self.state = AppState()
        patcher = mock.patch.object(control_callbacks, "app_state", self.state)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_pagination_bounds_come_back(self):
        result = self.restore_pagination({"symbols": ["NVDA", "AAPL", "MSFT"]})

        self.assertEqual(result, (3, 1, 3, 1))

    def test_the_symbol_states_are_rebuilt(self):
        self.restore_pagination({"symbols": ["NVDA", "AAPL"]})

        self.assertEqual(sorted(self.state.symbol_states), ["AAPL", "NVDA"])

    def test_the_first_symbol_becomes_the_current_one(self):
        self.restore_pagination({"symbols": ["NVDA", "AAPL"]})

        self.assertEqual(self.state.current_symbol, "NVDA")

    def test_existing_symbol_states_are_left_alone(self):
        self.state.init_symbol_state("NVDA")
        self.state.current_symbol = "NVDA"
        marker = self.state.symbol_states["NVDA"]

        self.restore_pagination({"symbols": ["NVDA"]})

        self.assertIs(self.state.symbol_states["NVDA"], marker)

    def test_no_stored_run_falls_back_to_a_single_page(self):
        self.assertEqual(self.restore_pagination(None), (1, 1, 1, 1))
        self.assertEqual(self.restore_pagination({}), (1, 1, 1, 1))

    def test_the_status_line_names_the_restored_run(self):
        message = self.restore_status(
            {
                "analysis_started": True,
                "symbols": ["NVDA", "AAPL"],
                "mode": "loop mode",
                "interval_text": " (every 15 minutes)",
            }
        )

        self.assertIn("NVDA, AAPL", message)
        self.assertIn("loop mode", message)
        self.assertIn("every 15 minutes", message)

    def test_a_refresh_with_no_run_says_nothing(self):
        self.assertEqual(self.restore_status(None), "")
        self.assertEqual(self.restore_status({"analysis_started": False}), "")

    def test_a_started_run_with_no_symbols_says_nothing(self):
        self.assertEqual(
            self.restore_status({"analysis_started": True, "symbols": []}), ""
        )


if __name__ == "__main__":
    unittest.main()


class SchedulerThreadFixture(ControlButtonFixture):
    """Runs the captured analysis thread with the stop flag armed after one
    pass, so each scheduling loop executes exactly one iteration."""

    def setUp(self):
        super().setUp()
        self.analyzed = []

        def start_analysis(symbol, *args, **kwargs):
            self.analyzed.append(symbol)
            if self._stop_after_each:
                self._arm_stop()

        patcher = mock.patch.object(
            control_callbacks, "start_analysis", start_analysis
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        sleep = mock.patch.object(control_callbacks.time, "sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

        self._stop_after_each = True

    def _arm_stop(self):
        self.state.stop_loop = True
        self.state.stop_market_hour = True
        self.state.stop_screener = True

    def _run_thread(self):
        self.assertEqual(len(Thread.started), 1)
        Thread.started[0]()


class LoopModeThreadTests(SchedulerThreadFixture):
    def test_the_loop_analyzes_every_symbol_in_the_batch(self):
        self._stop_after_each = False
        self._click(tickers="NVDA,AAPL", loop_enabled=True)
        # Stop once the queue has drained rather than mid-batch.
        original = self.state.get_next_symbol

        def draining():
            symbol = original()
            if not self.state.analysis_queue:
                self._arm_stop()
            return symbol

        self.state.get_next_symbol = draining

        self._run_thread()

        self.assertEqual(sorted(self.analyzed), ["AAPL", "NVDA"])

    def test_the_loop_is_registered_with_its_configuration(self):
        self._click(tickers="NVDA", loop_enabled=True, research_depth="Deep")

        self._run_thread()

        self.assertEqual(self.state.loop_config["research_depth"], "Deep")

    def test_a_failing_symbol_does_not_take_the_schedule_down(self):
        """The thread's finally clears analysis_running, so an escaping
        exception would silently end an unattended run."""
        self._stop_after_each = False
        analyzed = []

        def explode(symbol, *args, **kwargs):
            analyzed.append(symbol)
            self._arm_stop()
            raise RuntimeError("provider outage")

        with mock.patch.object(control_callbacks, "start_analysis", explode):
            self._click(tickers="NVDA", loop_enabled=True)
            self._run_thread()

        self.assertEqual(analyzed, ["NVDA"])

    def test_the_run_flag_is_cleared_when_the_thread_ends(self):
        self._click(tickers="NVDA", loop_enabled=True)

        self._run_thread()

        self.assertFalse(self.state.analysis_running)

    def test_the_batch_is_requeued_each_iteration(self):
        """Otherwise the second pass finds an empty queue and does nothing."""
        self._stop_after_each = False
        self._click(tickers="NVDA", loop_enabled=True)
        passes = []

        original = self.state.get_next_symbol

        def counting():
            symbol = original()
            if symbol:
                passes.append(symbol)
            if len(passes) >= 2:
                self._arm_stop()
            return symbol

        self.state.get_next_symbol = counting

        self._run_thread()

        self.assertEqual(passes, ["NVDA", "NVDA"])


class MarketHourThreadTests(SchedulerThreadFixture):
    def setUp(self):
        super().setUp()
        import webui.utils.market_hours as market_hours

        self.market_hours = market_hours
        self.open_checks = []

        # The scheduler waits until the next configured hour; make it now.
        patcher = mock.patch.object(
            market_hours, "get_next_market_datetime", lambda hour, now: now
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _market(self, is_open, reason="closed"):
        def check():
            self.open_checks.append(is_open)
            # The closed branch loops back around without analyzing, so the
            # stop flag has to be armed from here.
            if not is_open:
                self._arm_stop()
            return is_open, reason

        patcher = mock.patch.object(self.market_hours, "is_market_open", check)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_schedule_is_registered_with_its_hours(self):
        self._market(True)
        self._click(
            tickers="NVDA", market_hour_enabled=True, market_hours_input="9,15"
        )

        self._run_thread()

        self.assertEqual(self.state.market_hours, [9, 15])

    def test_nothing_runs_while_the_market_is_closed(self):
        self._market(False, "holiday")
        self._click(tickers="NVDA", market_hour_enabled=True)

        self._run_thread()

        self.assertEqual(self.analyzed, [])
        self.assertTrue(self.open_checks)

    def test_the_batch_runs_once_the_market_opens(self):
        self._market(True, "open")
        self._click(tickers="NVDA", market_hour_enabled=True)

        self._run_thread()

        self.assertEqual(self.analyzed, ["NVDA"])

    def test_the_configuration_reaches_the_scheduled_run(self):
        self._market(True, "open")
        self._click(
            tickers="NVDA", market_hour_enabled=True, research_depth="Deep"
        )

        self._run_thread()

        self.assertEqual(self.state.market_hour_config["research_depth"], "Deep")

    def test_the_run_flag_is_cleared_when_the_schedule_ends(self):
        self._market(True, "open")
        self._click(tickers="NVDA", market_hour_enabled=True)

        self._run_thread()

        self.assertFalse(self.state.analysis_running)


class ScreenerThreadTests(SchedulerThreadFixture):
    def _scan(self, candidates, *, error=None):
        def run_scan(asset_filter=None, **kwargs):
            if error:
                raise error
            return {"candidates": list(candidates.get(asset_filter, []))}

        return run_scan

    def _positions(self, owned=(), pending=(), error=None):
        utils = mock.MagicMock()
        if error:
            utils.get_positions_data.side_effect = error
            utils.get_open_orders.side_effect = error
        else:
            utils.get_positions_data.return_value = [{"symbol": s} for s in owned]
            utils.get_open_orders.return_value = set(pending)
        return utils

    def _run_screener(self, run_scan, utils=None):
        self._click(tickers="", screener_enabled=True)
        with mock.patch("tradingagents.screener.run_scan", run_scan):
            with mock.patch(
                "tradingagents.dataflows.alpaca_utils.AlpacaUtils",
                utils or self._positions(),
            ):
                self._run_thread()

    def test_stock_candidates_are_fed_to_the_pipeline(self):
        self._run_screener(
            self._scan({"stock": [{"symbol": "NVDA"}], "crypto": []})
        )

        self.assertEqual(self.analyzed, ["NVDA"])

    def test_crypto_candidates_are_fed_too(self):
        self._run_screener(
            self._scan({"stock": [], "crypto": [{"symbol": "BTC/USD"}]})
        )

        self.assertEqual(self.analyzed, ["BTC/USD"])

    def test_both_scans_contribute_to_one_batch(self):
        self._stop_after_each = False
        original_next = None

        run_scan = self._scan(
            {"stock": [{"symbol": "NVDA"}], "crypto": [{"symbol": "BTC/USD"}]}
        )
        self._click(tickers="", screener_enabled=True)

        def draining():
            symbol = self.state.analysis_queue.pop(0) if self.state.analysis_queue else None
            if not self.state.analysis_queue:
                self._arm_stop()
            return symbol

        self.state.get_next_symbol = draining
        with mock.patch("tradingagents.screener.run_scan", run_scan):
            with mock.patch(
                "tradingagents.dataflows.alpaca_utils.AlpacaUtils", self._positions()
            ):
                self._run_thread()

        self.assertEqual(sorted(self.analyzed), ["BTC/USD", "NVDA"])

    def test_an_overview_tab_is_created_for_the_screener_itself(self):
        self._run_screener(self._scan({"stock": [{"symbol": "NVDA"}], "crypto": []}))

        self.assertIn("SCREENER", self.state.symbol_states)

    def test_the_screener_placeholder_is_never_analyzed(self):
        self._stop_after_each = False
        self._click(tickers="", screener_enabled=True)

        def draining():
            symbol = self.state.analysis_queue.pop(0) if self.state.analysis_queue else None
            if not self.state.analysis_queue:
                self._arm_stop()
            return symbol

        self.state.get_next_symbol = draining
        with mock.patch(
            "tradingagents.screener.run_scan",
            self._scan({"stock": [{"symbol": "SCREENER"}, {"symbol": "NVDA"}], "crypto": []}),
        ):
            with mock.patch(
                "tradingagents.dataflows.alpaca_utils.AlpacaUtils", self._positions()
            ):
                self._run_thread()

        self.assertEqual(self.analyzed, ["NVDA"])

    def test_owned_and_pending_symbols_are_passed_to_the_scan(self):
        captured = {}

        def run_scan(asset_filter=None, **kwargs):
            captured[asset_filter] = kwargs
            self._arm_stop()
            return {"candidates": []}

        self._run_screener(
            run_scan, utils=self._positions(owned=["nvda"], pending=["AAPL"])
        )

        self.assertEqual(captured["stock"]["owned_symbols"], {"NVDA"})
        self.assertEqual(captured["stock"]["pending_symbols"], {"AAPL"})

    def test_an_unreachable_broker_does_not_stop_the_scan(self):
        captured = {}

        def run_scan(asset_filter=None, **kwargs):
            captured[asset_filter] = kwargs
            self._arm_stop()
            return {"candidates": []}

        self._run_screener(
            run_scan, utils=self._positions(error=RuntimeError("broker down"))
        )

        self.assertEqual(captured["stock"]["owned_symbols"], set())

    def test_a_failing_scan_does_not_stop_the_screener(self):
        calls = []

        def run_scan(asset_filter=None, **kwargs):
            calls.append(asset_filter)
            self._arm_stop()
            raise RuntimeError("yahoo down")

        self._run_screener(run_scan)

        self.assertIn("stock", calls)
        self.assertEqual(self.analyzed, [])

    def test_an_analyzed_candidate_goes_on_cooldown(self):
        self._run_screener(self._scan({"stock": [{"symbol": "NVDA"}], "crypto": []}))

        self.assertIn("NVDA", self.state.get_cooldown_map())
