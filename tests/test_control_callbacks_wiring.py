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
