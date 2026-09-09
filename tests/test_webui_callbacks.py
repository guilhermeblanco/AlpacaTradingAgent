"""Tests for the registered Dash callbacks.

Dash wraps each callback in a dispatcher that needs a live request, so
these reach past it to the function itself. What matters is that each one
renders from whatever state exists — including none — rather than raising
into the browser.
"""

from __future__ import annotations

import unittest
from unittest import mock

import dash

from conftest import dash_callback
from webui.utils.state import AppState


def _app(register):
    app = dash.Dash(__name__, suppress_callback_exceptions=True)
    register(app)
    return app


class StateFixture(unittest.TestCase):
    MODULES: tuple[str, ...] = ()

    def setUp(self):
        self.state = AppState()
        for module in self.MODULES:
            patch = mock.patch(f"{module}.app_state", self.state)
            patch.start()
            self.addCleanup(patch.stop)

    def _prepare(self, symbol="NVDA"):
        self.state.init_symbol_state(symbol)
        self.state.current_symbol = symbol
        self.state.analyzing_symbol = symbol
        self.state.active_analysts = ["Market Analyst", "News Analyst"]
        return self.state.get_state(symbol)


class StatusCallbackTests(StateFixture):
    MODULES = ("webui.callbacks.status_callbacks",)

    def setUp(self):
        super().setUp()
        from webui.callbacks.status_callbacks import register_status_callbacks

        self.app = _app(register_status_callbacks)

    def test_the_status_table_renders_before_a_run_starts(self):
        callback = dash_callback(self.app, "status-table.children")

        self.assertIsNotNone(callback(0, None))

    def test_the_status_table_lists_the_selected_analysts(self):
        self._prepare()
        self.state.update_agent_status("Market Analyst", "completed")
        callback = dash_callback(self.app, "status-table.children")

        rendered = str(callback(1, None))

        self.assertIn("Market Analyst", rendered)

    def test_the_progress_counters_are_reported(self):
        self.state.tool_calls_count = 4
        self.state.llm_calls_count = 2
        self.state.generated_reports_count = 1
        callback = dash_callback(self.app, "tool-calls-text.children")

        tools, llms, reports = callback(1)

        self.assertIn("4", tools)
        self.assertIn("2", llms)
        self.assertIn("1", reports)

    def test_the_fast_interval_is_off_while_idle(self):
        callback = dash_callback(self.app, "refresh-interval.disabled")

        fast_disabled, _medium, _text, _class = callback({}, 1)

        self.assertTrue(fast_disabled)

    def test_the_fast_interval_runs_during_an_analysis(self):
        self.state.analysis_running = True
        callback = dash_callback(self.app, "refresh-interval.disabled")

        fast_disabled, _medium, _text, _class = callback({}, 1)

        self.assertFalse(fast_disabled)

    def test_a_pending_ui_update_also_wakes_the_fast_interval(self):
        self.state.needs_ui_update = True
        callback = dash_callback(self.app, "refresh-interval.disabled")

        fast_disabled, *_rest = callback({}, 1)

        self.assertFalse(fast_disabled)


class ChartCallbackTests(StateFixture):
    MODULES = ("webui.callbacks.chart_callbacks",)

    def setUp(self):
        super().setUp()
        from webui.callbacks.chart_callbacks import register_chart_callbacks

        self.app = _app(register_chart_callbacks)

    def test_the_symbol_pager_is_empty_before_a_run(self):
        callback = dash_callback(self.app, "chart-pagination-container.children")

        self.assertIsNotNone(callback({}, 0))

    def test_the_symbol_pager_lists_analyzed_symbols(self):
        self._prepare("NVDA")
        self._prepare("AAPL")
        callback = dash_callback(self.app, "chart-pagination-container.children")

        rendered = str(callback({"symbols": ["NVDA", "AAPL"]}, 1))

        self.assertIn("NVDA", rendered)

    def test_no_symbol_yields_the_welcome_chart(self):
        callback = dash_callback(self.app, "chart-container.figure")

        with mock.patch(
            "webui.callbacks.chart_callbacks.create_welcome_chart", lambda: "welcome"
        ), mock.patch(
            "webui.callbacks.chart_callbacks.ctx", mock.Mock(triggered_id=None, triggered=[])
        ):
            figure, _label, _store = callback(0, 0, 0, 0, 1, 0, {})

        self.assertEqual(figure, "welcome")

    def test_a_chart_failure_falls_back_and_says_so(self):
        self._prepare("NVDA")

        def explode(*_a, **_k):
            raise RuntimeError("provider down")

        callback = dash_callback(self.app, "chart-container.figure")
        with mock.patch(
            "webui.callbacks.chart_callbacks.create_chart", explode
        ), mock.patch(
            "webui.callbacks.chart_callbacks.create_welcome_chart", lambda: "welcome"
        ), mock.patch(
            "webui.callbacks.chart_callbacks.ctx", mock.Mock(triggered_id="period-1y", triggered=[{"prop_id": "period-1y.n_clicks", "value": 1}])
        ):
            figure, label, _store = callback(
                0, 0, 0, 0, 1, 0, {"last_symbol": "NVDA", "selected_period": "1y"}
            )

        self.assertEqual(figure, "welcome")
        self.assertIn("NVDA", label)

    def test_exactly_one_period_button_is_active(self):
        callback = dash_callback(self.app, "period-1d.active")

        with mock.patch(
            "webui.callbacks.chart_callbacks.ctx", mock.Mock(triggered_id="period-1w")
        ):
            active = callback(0, 1, 0, 0)

        self.assertEqual(len(active), 4)
        self.assertEqual(sum(bool(flag) for flag in active), 1)
        self.assertTrue(active[1])

    def test_the_default_active_period_is_one_year(self):
        callback = dash_callback(self.app, "period-1d.active")

        with mock.patch(
            "webui.callbacks.chart_callbacks.ctx", mock.Mock(triggered_id=None)
        ):
            active = callback(0, 0, 0, 0)

        self.assertTrue(active[3])

    def test_the_chart_timestamp_renders(self):
        callback = dash_callback(self.app, "chart-last-updated.children")

        self.assertIsNotNone(callback({"last_symbol": "NVDA"}))


class StorageCallbackTests(StateFixture):
    MODULES = ()

    def setUp(self):
        super().setUp()
        from webui.callbacks.storage_callbacks import register_storage_callbacks

        self.app = _app(register_storage_callbacks)

    def test_every_storage_callback_registers(self):
        self.assertTrue(self.app.callback_map)


class TradingCallbackTests(StateFixture):
    MODULES = ()

    def setUp(self):
        super().setUp()
        from webui.callbacks.trading_callbacks import register_trading_callbacks

        self.app = _app(register_trading_callbacks)

    def test_the_account_tables_render_when_the_broker_is_unreachable(self):
        """No credentials is the default state for a fresh checkout."""
        callback = dash_callback(self.app, "positions-table-container.children")

        positions, orders, pagination, summary = callback(1, None, None, 1)

        for panel in (positions, orders, pagination, summary):
            self.assertIsNotNone(panel)

    def test_the_requested_orders_page_is_honoured(self):
        captured = {}

        def page(page=1, page_size=None):
            captured["page"] = page
            return {"orders": [], "page": page, "total_pages": 1, "total_orders": 0}

        callback = dash_callback(self.app, "positions-table-container.children")
        with mock.patch(
            "webui.callbacks.trading_callbacks.AlpacaUtils.get_recent_orders_page", page
        ):
            callback(1, None, None, 3)

        self.assertEqual(captured["page"], 3)

    def test_a_missing_page_falls_back_to_the_first(self):
        captured = {}

        def page(page=1, page_size=None):
            captured["page"] = page
            return {"orders": [], "page": page, "total_pages": 1, "total_orders": 0}

        callback = dash_callback(self.app, "positions-table-container.children")
        with mock.patch(
            "webui.callbacks.trading_callbacks.AlpacaUtils.get_recent_orders_page", page
        ):
            callback(1, None, None, None)

        self.assertEqual(captured["page"], 1)


class CostCallbackTests(StateFixture):
    MODULES = ()

    def setUp(self):
        super().setUp()
        from webui.callbacks.cost_callbacks import register_cost_callbacks

        self.app = _app(register_cost_callbacks)

    def test_the_panel_renders_with_no_recorded_spend(self):
        callback = dash_callback(self.app, "cost-summary-cards.children")

        with mock.patch(
            "tradingagents.llm_cost.scan_run_costs", lambda *a, **k: []
        ), mock.patch(
            "tradingagents.llm_cost.aggregate_costs",
            lambda *a, **k: {
                "per_day": {},
                "per_symbol": {},
                "per_model": {},
                "totals": {
                    "runs": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                },
            },
        ), mock.patch(
            "tradingagents.llm_cost.realized_returns_by_symbol", lambda *a, **k: {}
        ):
            result = callback(1)

        self.assertIsNotNone(result)


class BacktestCallbackTests(StateFixture):
    MODULES = ()

    def setUp(self):
        super().setUp()
        from webui.callbacks.backtest_callbacks import register_backtest_callbacks

        self.app = _app(register_backtest_callbacks)

    def test_every_backtest_callback_registers(self):
        outputs = " ".join(self.app.callback_map)

        self.assertIn("backtest", outputs)


class ApiConfigCallbackTests(StateFixture):
    MODULES = ()

    def setUp(self):
        super().setUp()
        from webui.callbacks.api_config_callbacks import register_api_config_callbacks

        self.app = _app(register_api_config_callbacks)

    def test_the_modal_opens_and_closes(self):
        callback = dash_callback(self.app, "api-config-modal.is_open")

        with mock.patch(
            "webui.callbacks.api_config_callbacks.ctx",
            mock.Mock(triggered_id="open-api-config-btn"),
        ):
            self.assertTrue(callback(1, None, False))

        with mock.patch(
            "webui.callbacks.api_config_callbacks.ctx",
            mock.Mock(triggered_id="close-api-config-btn"),
        ):
            self.assertFalse(callback(None, 1, True))

    def test_a_broker_connection_test_reports_its_checks(self):
        from tradingagents.broker.preflight import (
            BrokerPreflightReport,
            CheckStatus,
            PreflightCheck,
        )

        report = BrokerPreflightReport(
            broker="alpaca",
            symbol="AAPL",
            checks=[
                PreflightCheck(
                    name="capabilities",
                    status=CheckStatus.PASS,
                    message="Broker capability contract loaded.",
                )
            ],
        )
        callback = dash_callback(self.app, "integration-health-results.children")

        with mock.patch(
            "tradingagents.broker.registry.get_execution_broker_runtime",
            lambda _config: mock.MagicMock(),
        ), mock.patch(
            "tradingagents.broker.preflight.certify_broker_runtime",
            lambda *a, **k: report,
        ):
            rendered = str(callback(1, "alpaca"))

        self.assertIn("capability contract", rendered)

    def test_an_unreachable_broker_is_reported_rather_than_raised(self):
        callback = dash_callback(self.app, "integration-health-results.children")

        with mock.patch(
            "tradingagents.broker.registry.get_execution_broker_runtime",
            side_effect=RuntimeError("no credentials"),
        ):
            rendered = str(callback(1, "alpaca"))

        self.assertIn("no credentials", rendered)


class ReportCallbackTests(StateFixture):
    MODULES = ("webui.callbacks.report_callbacks",)

    def setUp(self):
        super().setUp()
        from webui.callbacks.report_callbacks import register_report_callbacks

        self.app = _app(register_report_callbacks)

    def test_the_symbol_pager_renders_before_a_run(self):
        callback = dash_callback(self.app, "report-pagination-container.children")

        self.assertIsNotNone(callback({}, 0))

    def test_the_tabs_render_for_an_analyzed_symbol(self):
        state = self._prepare()
        state["current_reports"]["market_report"] = "## Market\n\nMomentum is positive."
        callback = dash_callback(self.app, "market-analysis-tab-content.children")

        rendered = callback(1, 1)

        self.assertIsNotNone(rendered)

    def test_the_decision_summary_renders_without_a_decision(self):
        self._prepare()
        callback = dash_callback(self.app, "decision-summary.children")

        self.assertIsNotNone(callback(1, 1))

    def test_the_researcher_debate_renders(self):
        state = self._prepare()
        state["investment_debate_state"] = {
            "history": "🐂 Bull Researcher\nUpside.\n🐻 Bear Researcher\nDownside."
        }
        callback = dash_callback(self.app, "researcher-debate-tab-content.children")

        rendered = str(callback(1, 1))

        self.assertIsNotNone(rendered)

    def test_the_risk_debate_renders(self):
        state = self._prepare()
        state["risk_debate_state"] = {"history": "Risky Analyst: Press on."}
        callback = dash_callback(self.app, "risk-debate-tab-content.children")

        self.assertIsNotNone(callback(1, 1))


if __name__ == "__main__":
    unittest.main()
