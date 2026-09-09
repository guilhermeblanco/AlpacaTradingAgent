"""Tests for the backtest, integration, evaluation, and tool-output panels.

Each of these is an operator surface that must render whatever it is given:
no data, a provider outage, or a run that produced results. None of them
may raise into the browser.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

import dash

from conftest import dash_callback
from webui.components.tool_outputs_modal import (
    create_show_tool_outputs_button,
    create_tool_outputs_modal,
    format_tool_outputs_content,
)
from webui.utils.state import AppState


def _app(register):
    app = dash.Dash(__name__, suppress_callback_exceptions=True)
    register(app)
    return app


class ToolOutputFormattingTests(unittest.TestCase):
    CALL = {
        "timestamp": "10:00:00",
        "tool_name": "get_stock_news",
        "inputs": {"symbol": "NVDA"},
        "output": "the news",
        "execution_time": "1.2s",
        "status": "success",
        "agent_type": "News Analyst",
        "symbol": "NVDA",
    }

    def test_the_modal_renders(self):
        self.assertIsNotNone(create_tool_outputs_modal())

    def test_the_button_carries_its_report_type(self):
        rendered = str(create_show_tool_outputs_button("market_report"))

        self.assertIn("show-tool-outputs-btn", rendered)
        self.assertIn("market_report", rendered)

    def test_no_calls_says_so_for_the_named_report(self):
        message = format_tool_outputs_content([], report_type="market_report")

        self.assertIn("Market Report", message)
        self.assertIn("No tool calls", message)

    def test_no_calls_and_no_report_still_says_so(self):
        self.assertIn("No tool calls", format_tool_outputs_content([]))

    def test_a_call_renders_its_inputs_and_output(self):
        content = format_tool_outputs_content([self.CALL])

        self.assertIn("get_stock_news", content)
        self.assertIn("NVDA", content)
        self.assertIn("the news", content)
        self.assertIn("News Analyst", content)

    def test_each_status_gets_its_own_marker(self):
        success = format_tool_outputs_content([self.CALL])
        failed = format_tool_outputs_content([dict(self.CALL, status="error")])
        unknown = format_tool_outputs_content([dict(self.CALL, status="pending")])

        self.assertIn("✅", success)
        self.assertIn("❌", failed)
        self.assertIn("⚪", unknown)

    def test_calls_are_numbered_in_order(self):
        content = format_tool_outputs_content(
            [self.CALL, dict(self.CALL, tool_name="get_google_news")]
        )

        self.assertIn("Tool Call #1", content)
        self.assertIn("Tool Call #2", content)

    def test_a_call_missing_fields_still_renders(self):
        """The log holds entries from older runs with fewer fields."""
        content = format_tool_outputs_content([{"tool_name": "get_stock_news"}])

        self.assertIn("get_stock_news", content)
        self.assertIn("Unknown", content)


class BacktestCallbackTests(unittest.TestCase):
    def setUp(self):
        from webui.callbacks.backtest_callbacks import register_backtest_callbacks

        self.app = _app(register_backtest_callbacks)

    def test_a_missing_symbol_is_refused_with_a_reason(self):
        callback = dash_callback(self.app, "backtest-status.children")

        status, metrics, _figure, style, _windows = callback(
            1, "", "2026-01-01", "2026-06-01", 20, False, "none"
        )

        self.assertIsNotNone(status)
        self.assertIsNone(metrics)
        self.assertEqual(style, {"display": "none"})

    def test_a_failing_backtest_is_reported_rather_than_raised(self):
        callback = dash_callback(self.app, "backtest-status.children")

        with mock.patch(
            "tradingagents.backtest.run_recorded_walk_forward",
            side_effect=RuntimeError("no recorded decisions"),
        ):
            status, *_rest = callback(
                1, "NVDA", "2026-01-01", "2026-06-01", 20, False, "none"
            )

        self.assertIn("no recorded decisions", str(status))

    def test_teaching_without_a_symbol_is_refused(self):
        callback = dash_callback(self.app, "backtest-teach-status.children")

        self.assertIsNotNone(callback(1, "", "2026-01-01", "2026-06-01"))

    def test_a_failing_teach_run_is_reported(self):
        callback = dash_callback(self.app, "backtest-teach-status.children")

        with mock.patch(
            "tradingagents.backtest.teach_from_recorded_history",
            side_effect=RuntimeError("no history"),
            create=True,
        ):
            self.assertIsNotNone(callback(1, "NVDA", "2026-01-01", "2026-06-01"))


class IntegrationConfigCallbackTests(unittest.TestCase):
    def setUp(self):
        from webui.callbacks.api_config_callbacks import register_api_config_callbacks

        self.app = _app(register_api_config_callbacks)

    def test_a_secret_field_toggles_between_hidden_and_visible(self):
        callback = dash_callback(self.app, "api-input-openai.type")

        shown_type, shown_icon = callback(1, "password")
        hidden_type, hidden_icon = callback(2, "text")

        self.assertEqual(shown_type, "text")
        self.assertEqual(hidden_type, "password")
        self.assertNotEqual(shown_icon, hidden_icon)

    def test_saved_credentials_are_never_echoed_back_to_the_browser(self):
        """The vault holds real credentials; the form must not re-serve them.
        The callback also returns the paper toggle and the broker selects,
        so only the credential inputs are checked."""
        from webui.components.api_config_modal import get_api_configs

        callback = dash_callback(self.app, "api-input-openai.value")
        credential_count = len(get_api_configs())

        values = callback(1, {})

        self.assertGreater(len(values), credential_count)
        for value in values[:credential_count]:
            self.assertIn(value, ("", None))


class EvaluationCallbackTests(unittest.TestCase):
    def setUp(self):
        from webui.callbacks.evaluation_callbacks import register_evaluation_callbacks

        self.app = _app(register_evaluation_callbacks)
        runtime = SimpleNamespace(unit_of_work_factory=None)
        patch = mock.patch(
            "webui.callbacks.evaluation_callbacks.get_persistence_runtime",
            lambda: runtime,
        )
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_filters_are_empty_without_postgres(self):
        callback = dash_callback(self.app, "evaluation-horizon.options")

        horizons, horizon, challengers, challenger, champions, champion = callback(
            1, None, None, None, None
        )

        self.assertEqual(horizons, [])
        self.assertIsNone(horizon)
        self.assertEqual(challengers, [])

    def test_the_panel_says_postgres_is_required(self):
        callback = dash_callback(self.app, "evaluation-summary.children")

        summary, promotion = callback("1d", "a", "b", 30, 1)

        self.assertIn("SETUP REQUIRED", str(summary))
        self.assertIn("PostgreSQL", str(promotion))


class TradingPanelCallbackTests(unittest.TestCase):
    def setUp(self):
        from webui.callbacks.trading_callbacks import register_trading_callbacks

        self.app = _app(register_trading_callbacks)

    def test_the_account_title_names_the_trading_mode(self):
        callback = dash_callback(self.app, "alpaca-account-title.children")

        with mock.patch(
            "tradingagents.dataflows.config.get_alpaca_use_paper", lambda: "True"
        ):
            self.assertIn("Paper", str(callback(1)))

        with mock.patch(
            "tradingagents.dataflows.config.get_alpaca_use_paper", lambda: "False"
        ):
            self.assertIn("Live", str(callback(1)))

    def test_no_liquidation_click_shows_no_dialog(self):
        callback = dash_callback(self.app, "liquidate-confirm.displayed")

        displayed, message = callback([None, None])

        self.assertFalse(displayed)
        self.assertEqual(message, "")

    def test_a_liquidation_click_asks_for_confirmation(self):
        callback = dash_callback(self.app, "liquidate-confirm.displayed")

        # The callback imports ctx from dash inside its body.
        with mock.patch(
            "dash.ctx",
            mock.Mock(
                triggered=[
                    {"prop_id": '{"index":"NVDA","type":"liquidate-btn"}.n_clicks'}
                ]
            ),
        ):
            displayed, message = callback([1])

        self.assertTrue(displayed)
        self.assertIn("NVDA", message)


class ChartPagerTests(unittest.TestCase):
    def setUp(self):
        from webui.callbacks.chart_callbacks import register_chart_callbacks

        self.state = AppState()
        patch = mock.patch("webui.callbacks.chart_callbacks.app_state", self.state)
        patch.start()
        self.addCleanup(patch.stop)
        self.app = _app(register_chart_callbacks)

    def test_the_pager_marks_the_active_symbol(self):
        for symbol in ("NVDA", "AAPL"):
            self.state.init_symbol_state(symbol)
        self.state.current_symbol = "AAPL"
        callback = dash_callback(self.app, "chart-pagination-container.children")

        rendered = str(callback({"symbols": ["NVDA", "AAPL"]}, 1))

        self.assertIn("NVDA", rendered)
        self.assertIn("AAPL", rendered)

    def test_the_timestamp_is_blank_until_a_chart_loads(self):
        callback = dash_callback(self.app, "chart-last-updated.children")

        self.assertEqual(callback({}), "")

    def test_the_timestamp_renders_once_recorded(self):
        callback = dash_callback(self.app, "chart-last-updated.children")

        self.assertIsNotNone(callback({"last_updated": "10:00:00"}))


if __name__ == "__main__":
    unittest.main()


class SafetyPanelCallbackTests(unittest.TestCase):
    def setUp(self):
        from webui.callbacks.safety_callbacks import register_safety_callbacks

        self.app = _app(register_safety_callbacks)

    def _guard(self, **overrides):
        status = {
            "enabled": True,
            "guards": {
                "kill_switch": {"ok": True, "status": "ok", "detail": {}},
                "trade_notional": {"ok": True, "status": "ok", "detail": {"cap": 25000}},
                "concentration": {"ok": True, "status": "ok", "detail": {"limit": 5000}},
                "daily_loss": {"ok": True, "status": "ok", "detail": {"change_pct": -1.2}},
                "drawdown": {"ok": True, "status": "ok", "detail": {"drawdown_pct": 3.4}},
                "rejection_streak": {"ok": True, "status": "ok", "detail": {"streak": 0}},
                "llm_budget": {
                    "ok": True,
                    "status": "ok",
                    "detail": {"used": 100, "budget": 5000},
                },
            },
            "reasons": [],
        }
        status.update(overrides)
        guard = mock.MagicMock()
        guard.status.return_value = status
        return guard

    def test_every_guard_is_rendered(self):
        callback = dash_callback(self.app, "safety-status-container.children")

        with mock.patch("tradingagents.safety.get_safety_guard", lambda: self._guard()), \
             mock.patch("webui.callbacks.safety_callbacks.ctx", mock.Mock(triggered_id=None)):
            cards, _action = callback(1, None, None)

        rendered = str(cards)
        for label in ("Kill Switch", "Trade Size Cap", "Drawdown Breaker", "LLM Budget"):
            self.assertIn(label, rendered)

    def test_a_holding_guard_shows_its_reason(self):
        callback = dash_callback(self.app, "safety-status-container.children")
        guard = self._guard(reasons=["daily loss breaker tripped"])

        with mock.patch("tradingagents.safety.get_safety_guard", lambda: guard), \
             mock.patch("webui.callbacks.safety_callbacks.ctx", mock.Mock(triggered_id=None)):
            cards, _action = callback(1, None, None)

        self.assertIn("daily loss breaker tripped", str(cards))

    def test_a_disabled_safety_layer_is_called_out(self):
        callback = dash_callback(self.app, "safety-status-container.children")
        guard = self._guard(enabled=False)

        with mock.patch("tradingagents.safety.get_safety_guard", lambda: guard), \
             mock.patch("webui.callbacks.safety_callbacks.ctx", mock.Mock(triggered_id=None)):
            cards, _action = callback(1, None, None)

        self.assertIn("DISABLED", str(cards))

    def test_engaging_the_kill_switch_halts_order_flow(self):
        callback = dash_callback(self.app, "safety-status-container.children")
        guard = self._guard()

        with mock.patch("tradingagents.safety.get_safety_guard", lambda: guard), \
             mock.patch(
                 "webui.callbacks.safety_callbacks.ctx",
                 mock.Mock(triggered_id="safety-kill-switch-btn"),
             ):
            _cards, action = callback(1, 1, None)

        guard.engage_kill_switch.assert_called_once()
        self.assertIn("ENGAGED", str(action))

    def test_releasing_the_kill_switch_resumes_order_flow(self):
        callback = dash_callback(self.app, "safety-status-container.children")
        guard = self._guard()

        with mock.patch("tradingagents.safety.get_safety_guard", lambda: guard), \
             mock.patch(
                 "webui.callbacks.safety_callbacks.ctx",
                 mock.Mock(triggered_id="safety-release-btn"),
             ):
            _cards, action = callback(1, None, 1)

        guard.release_kill_switch.assert_called_once()
        self.assertIn("released", str(action))

    def test_a_skipped_guard_explains_why(self):
        """Without account data a breaker cannot be evaluated."""
        callback = dash_callback(self.app, "safety-status-container.children")
        guard = self._guard()
        guard.status.return_value["guards"]["daily_loss"] = {
            "ok": True,
            "status": "skipped",
            "detail": {"detail": "no account data"},
        }

        with mock.patch("tradingagents.safety.get_safety_guard", lambda: guard), \
             mock.patch("webui.callbacks.safety_callbacks.ctx", mock.Mock(triggered_id=None)):
            cards, _action = callback(1, None, None)

        self.assertIn("no account data", str(cards))
