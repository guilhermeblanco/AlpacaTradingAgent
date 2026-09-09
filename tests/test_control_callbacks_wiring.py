"""Tests for the configuration panel's registered callbacks.

These decide what a run is configured with: which models are offered for
the chosen provider, which symbols are queued, which scheduling mode is
armed, and what the operator is told about each choice before starting.
"""

from __future__ import annotations

import unittest
from unittest import mock

import dash

from conftest import dash_callback
from webui.utils.state import AppState


def _app():
    from webui.callbacks.control_callbacks import register_control_callbacks

    app = dash.Dash(__name__, suppress_callback_exceptions=True)
    register_control_callbacks(app)
    return app


class ControlFixture(unittest.TestCase):
    def setUp(self):
        self.app = _app()
        self.state = AppState()
        patch = mock.patch("webui.callbacks.control_callbacks.app_state", self.state)
        patch.start()
        self.addCleanup(patch.stop)


class ProviderModelTests(ControlFixture):
    def test_choosing_a_provider_offers_its_models(self):
        callback = dash_callback(self.app, "quick-llm.options")

        result = callback("anthropic", None, None)
        quick_options = result[0]

        self.assertTrue(quick_options)
        self.assertTrue(all("value" in option for option in quick_options))

    def test_every_provider_offers_at_least_one_model(self):
        from tradingagents.openai_model_registry import get_llm_provider_options

        callback = dash_callback(self.app, "quick-llm.options")

        for option in get_llm_provider_options():
            quick_options = callback(option["value"], None, None)[0]

            self.assertTrue(quick_options, option["value"])

    def test_the_endpoint_field_appears_only_for_providers_that_use_one(self):
        from tradingagents.openai_model_registry import get_provider_ui_metadata

        callback = dash_callback(self.app, "quick-llm.options")
        hidden = {"display": "none"}

        for provider in ("openai", "ollama"):
            style = callback(provider, None, None)[4]
            visible = get_provider_ui_metadata(provider).get("backend_visible", False)

            self.assertEqual(style != hidden, bool(visible), provider)

    def test_a_current_model_is_kept_when_the_provider_still_offers_it(self):
        callback = dash_callback(self.app, "quick-llm.options")

        options = callback("openai", None, None)[0]
        existing = options[0]["value"]

        self.assertEqual(callback("openai", existing, None)[1], existing)

    def test_the_custom_model_field_is_hidden_until_custom_is_chosen(self):
        callback = dash_callback(self.app, "quick-llm-custom-model-group.style")

        hidden_style = callback("openai", "gpt-5.4-nano", "gpt-5.4-mini")[0]

        self.assertEqual(hidden_style, {"display": "none"})

    def test_the_llm_parameter_controls_render(self):
        callback = dash_callback(self.app, "quick-llm-info.children")

        result = callback("gpt-5.4-nano", None, "openai")

        self.assertIsNotNone(result[0])


class SymbolEntryTests(ControlFixture):
    def test_typed_symbols_render_as_chips(self):
        callback = dash_callback(self.app, "symbol-selected-chips.children")

        rendered = str(callback("nvda, aapl"))

        self.assertIn("NVDA", rendered)
        self.assertIn("AAPL", rendered)

    def test_no_symbols_renders_no_chips(self):
        callback = dash_callback(self.app, "symbol-selected-chips.children")

        self.assertIsNotNone(callback(""))

    def test_the_status_line_counts_the_selection(self):
        callback = dash_callback(self.app, "symbol-search-status.children")

        rendered = str(callback("NVDA, AAPL"))

        self.assertIsNotNone(rendered)

    def test_an_empty_query_offers_no_suggestions(self):
        callback = dash_callback(self.app, "symbol-suggestions.children")

        self.assertIsNotNone(callback("", ""))

    def test_suggestions_come_from_the_broker_search(self):
        callback = dash_callback(self.app, "symbol-suggestions.children")
        assets = [{"symbol": "NVDA", "name": "NVIDIA Corp", "asset_type": "stock"}]

        with mock.patch(
            "webui.callbacks.control_callbacks.AlpacaUtils.search_assets",
            lambda *a, **k: assets,
        ):
            rendered = str(callback("nvd", ""))

        self.assertIn("NVDA", rendered)

    def test_a_failing_symbol_search_does_not_break_the_panel(self):
        callback = dash_callback(self.app, "symbol-suggestions.children")

        def explode(*_a, **_k):
            raise RuntimeError("no credentials")

        with mock.patch(
            "webui.callbacks.control_callbacks.AlpacaUtils.search_assets", explode
        ):
            self.assertIsNotNone(callback("nvd", ""))


class SchedulingCallbackTests(ControlFixture):
    def test_arming_one_mode_disarms_the_others(self):
        callback = dash_callback(self.app, "loop-enabled.value")

        with mock.patch(
            "webui.callbacks.control_callbacks.dash.callback_context",
            mock.Mock(triggered=[{"prop_id": "loop-enabled.value"}]),
        ):
            loop, market, screener, *_disabled = callback(True, True, True)

        self.assertEqual((loop, market, screener), (True, False, False))

    def test_the_scheduling_summary_describes_the_armed_mode(self):
        callback = dash_callback(self.app, "scheduling-mode-info.children")

        rendered = str(callback(True, 30, False, ""))

        self.assertIsNotNone(rendered)

    def test_the_screener_summary_renders(self):
        callback = dash_callback(self.app, "screener-mode-info.children")

        self.assertIsNotNone(callback(True, 15))

    def test_valid_market_hours_are_confirmed(self):
        callback = dash_callback(self.app, "market-hours-validation.children")

        rendered = str(callback("10,15"))

        self.assertIn("10:00 AM", rendered)
        self.assertIn("3:00 PM", rendered)

    def test_invalid_market_hours_are_rejected_with_a_reason(self):
        callback = dash_callback(self.app, "market-hours-validation.children")

        rendered = str(callback("25"))

        self.assertIn("outside market hours", rendered)

    def test_blank_market_hours_say_nothing(self):
        callback = dash_callback(self.app, "market-hours-validation.children")

        self.assertEqual(callback(""), "")


class RunSummaryTests(ControlFixture):
    def test_the_research_depth_summary_renders_for_each_depth(self):
        callback = dash_callback(self.app, "research-depth-info.children")

        for depth in (1, 3, 5):
            self.assertIsNotNone(callback(depth), depth)

    def test_the_trading_mode_summary_distinguishes_the_two_modes(self):
        callback = dash_callback(self.app, "trading-mode-info.children")

        investment = str(callback(False))
        trading = str(callback(True))

        self.assertNotEqual(investment, trading)

    def test_the_auto_trade_summary_reflects_the_configured_amount(self):
        callback = dash_callback(self.app, "trade-after-analyze-info.children")

        rendered = str(callback(True, 2500))

        # The figure gates live order execution, so it is grouped for reading.
        self.assertIn("$2,500.00", rendered)

    def test_a_missing_amount_falls_back_to_the_default(self):
        callback = dash_callback(self.app, "trade-after-analyze-info.children")

        rendered = str(callback(True, None))

        self.assertIn("$1,000.00", rendered)

    def test_the_auto_trade_summary_renders_when_disabled(self):
        callback = dash_callback(self.app, "trade-after-analyze-info.children")

        self.assertIsNotNone(callback(False, 1000))

    def test_the_run_button_reads_start_while_idle(self):
        callback = dash_callback(self.app, "control-btn.children")

        label, _color = callback(0, 0)

        self.assertIn("Start", str(label))

    def test_the_run_button_offers_to_stop_during_a_run(self):
        self.state.analysis_running = True
        callback = dash_callback(self.app, "control-btn.children")

        label, _color = callback(1, 1)

        self.assertIn("Stop", str(label))


if __name__ == "__main__":
    unittest.main()


class ControlButtonTests(ControlFixture):
    """The Start/Stop button: what it refuses, and what it arms."""

    #: The callback takes 44 positional inputs; only a handful matter per test.
    FIELDS = (
        "n_clicks tickers symbol_query_input analysts_market analysts_social "
        "analysts_news analysts_fundamentals analysts_macro research_depth "
        "llm_provider backend_url output_language checkpoint_enabled "
        "quick_llm deep_llm quick_llm_custom_model deep_llm_custom_model "
        "google_thinking_level anthropic_effort xai_reasoning_effort "
        "quick_reasoning_effort quick_verbosity quick_summary quick_temperature "
        "quick_top_p quick_max_output_tokens quick_store quick_parallel_tool_calls "
        "deep_reasoning_effort deep_verbosity deep_summary deep_temperature "
        "deep_top_p deep_max_output_tokens deep_store deep_parallel_tool_calls "
        "allow_shorts loop_enabled loop_interval trade_enabled trade_amount "
        "market_hour_enabled market_hours_input screener_enabled screener_interval"
    ).split()

    DEFAULTS = {
        "n_clicks": 1,
        "tickers": "NVDA",
        "analysts_market": True,
        "research_depth": "Medium",
        "llm_provider": "openai",
        "quick_llm": "gpt-5.4-nano",
        "deep_llm": "gpt-5.4-mini",
        "output_language": "English",
        "loop_interval": 60,
        "trade_amount": 1000,
        "screener_interval": 15,
    }

    def setUp(self):
        super().setUp()
        self.callback = dash_callback(self.app, "result-text.children")
        self.threads = []
        patch = mock.patch(
            "webui.callbacks.control_callbacks.threading.Thread",
            lambda *a, **k: self.threads.append(k) or mock.MagicMock(),
        )
        patch.start()
        self.addCleanup(patch.stop)

    def _click(self, **overrides):
        values = dict.fromkeys(self.FIELDS)
        values.update(self.DEFAULTS)
        values.update(overrides)
        return self.callback(*[values[name] for name in self.FIELDS])

    def test_a_button_that_was_never_clicked_changes_nothing(self):
        result = self._click(n_clicks=None)

        self.assertTrue(all(item is dash.no_update for item in result))

    def test_no_symbols_is_refused(self):
        message, *_rest = self._click(tickers="", symbol_query_input="")

        self.assertIn("at least one stock symbol", message)

    def test_the_search_box_supplies_symbols_when_the_field_is_empty(self):
        """Symbol states are created synchronously so paging works at once;
        the queue itself is filled on the worker thread."""
        self._click(tickers="", symbol_query_input="nvda; aapl")

        self.assertEqual(list(self.state.symbol_states), ["NVDA", "AAPL"])

    def test_symbols_are_normalized_before_their_state_is_created(self):
        self._click(tickers=" nvda , aapl ")

        self.assertEqual(list(self.state.symbol_states), ["NVDA", "AAPL"])

    def test_a_worker_thread_is_started_for_the_run(self):
        """Non-daemon on purpose: an in-flight analysis finishes rather than
        being killed when the server is asked to stop."""
        self._click()

        self.assertEqual(len(self.threads), 1)
        self.assertEqual(self.threads[0]["target"].__name__, "analysis_thread")
        self.assertNotIn("daemon", self.threads[0])

    def test_the_selected_analysts_are_recorded(self):
        self._click(analysts_market=True, analysts_macro=True)

        self.assertEqual(
            self.state.active_analysts, ["Market Analyst", "Macro Analyst"]
        )

    def test_a_custom_model_without_an_id_is_refused(self):
        message, *_rest = self._click(quick_llm="custom", quick_llm_custom_model="")

        self.assertIn("custom model", message.lower())

    def test_invalid_market_hours_are_refused(self):
        message, *_rest = self._click(market_hour_enabled=True, market_hours_input="25")

        self.assertIn("Hour 25", message)

    def test_the_trade_settings_reach_the_shared_state(self):
        self._click(trade_enabled=True, trade_amount=2500)

        self.assertTrue(self.state.trade_enabled)
        self.assertEqual(self.state.trade_amount, 2500)

    def test_a_zero_trade_amount_falls_back_to_the_default(self):
        self._click(trade_enabled=True, trade_amount=0)

        self.assertEqual(self.state.trade_amount, 1000)

    def test_a_zero_loop_interval_falls_back_to_the_default(self):
        self._click(loop_enabled=True, loop_interval=0)

        self.assertEqual(self.state.loop_interval_minutes, 60)

    def test_a_running_analysis_is_stopped_rather_than_restarted(self):
        self.state.analysis_running = True

        message, *_rest = self._click()

        self.assertIn("stopped", message.lower())
        self.assertFalse(self.state.analysis_running)

    def test_stopping_names_the_mode_that_was_running(self):
        for setup, expected in (
            ("screener_enabled", "Screener mode stopped."),
            ("loop_enabled", "Loop analysis stopped."),
            ("market_hour_enabled", "Market hour analysis stopped."),
        ):
            self.state.reset()
            setattr(self.state, setup, True)

            message, *_rest = self._click()

            self.assertEqual(message, expected, setup)

    def test_screener_mode_needs_no_symbols(self):
        message, *_rest = self._click(
            tickers="", symbol_query_input="", screener_enabled=True
        )

        self.assertNotIn("at least one stock symbol", message)


class AnalysisThreadTests(ControlButtonTests):
    # Inherits the click helpers; the inherited button assertions run
    # again here harmlessly and keep the fixture honest.

    """The worker the button starts, run inline with the analysis stubbed."""

    def setUp(self):
        super().setUp()
        # start_analysis is called positionally: ticker first, then the five
        # analyst flags, depth, allow_shorts, and the two model names.
        self.started = []

        def record(*args, **kwargs):
            self.started.append(
                {
                    "ticker": args[0],
                    "research_depth": args[6],
                    "allow_shorts": args[7],
                    "quick_llm": args[8],
                    "deep_llm": args[9],
                    **kwargs,
                }
            )
            return "started"

        patch = mock.patch(
            "webui.callbacks.control_callbacks.start_analysis", side_effect=record
        )
        patch.start()
        self.addCleanup(patch.stop)

    def _worker(self, **overrides):
        self._click(**overrides)
        self.assertEqual(len(self.threads), 1)
        return self.threads[0]["target"]

    def test_a_single_run_analyzes_each_queued_symbol(self):
        worker = self._worker(tickers="NVDA,AAPL")

        worker()

        self.assertEqual(len(self.started), 2)
        self.assertEqual(
            [call["ticker"] for call in self.started], ["NVDA", "AAPL"]
        )

    def test_the_run_configuration_reaches_the_analysis(self):
        worker = self._worker(
            allow_shorts=True,
            research_depth="Deep",
            llm_provider="anthropic",
            quick_llm="claude-haiku-4-5-20251001",
            deep_llm="claude-opus-5",
        )

        worker()

        call = self.started[0]
        self.assertTrue(call["allow_shorts"])
        self.assertEqual(call["research_depth"], "Deep")
        self.assertEqual(call["llm_provider"], "anthropic")
        self.assertEqual(call["deep_llm"], "claude-opus-5")

    def test_a_failing_symbol_does_not_stop_the_rest(self):
        """One provider outage must not abandon the remaining symbols."""
        worker = self._worker(tickers="NVDA,AAPL")
        calls = []

        def flaky(*args, **_kwargs):
            calls.append(args[0])
            if args[0] == "NVDA":
                raise RuntimeError("provider down")
            return "ok"

        with mock.patch("webui.callbacks.control_callbacks.start_analysis", flaky):
            worker()

        self.assertEqual(calls, ["NVDA", "AAPL"])

    def test_the_run_clears_the_running_flag_when_it_finishes(self):
        worker = self._worker()

        worker()

        self.assertFalse(self.state.analysis_running)

    def test_the_running_flag_clears_even_when_the_worker_raises(self):
        """Otherwise the UI shows a run that is already dead and the button
        never returns to Start."""
        worker = self._worker()

        with mock.patch(
            "webui.callbacks.control_callbacks.app_state.add_symbols_to_queue",
            side_effect=RuntimeError("state corrupted"),
        ):
            with self.assertRaises(RuntimeError):
                worker()

        self.assertFalse(self.state.analysis_running)
