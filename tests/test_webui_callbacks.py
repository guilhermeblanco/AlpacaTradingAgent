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


class ReportNavigationTests(StateFixture):
    MODULES = ("webui.callbacks.report_callbacks",)

    def setUp(self):
        super().setUp()
        from webui.callbacks.report_callbacks import register_report_callbacks

        self.app = _app(register_report_callbacks)

    def test_the_symbol_label_names_the_paged_symbol(self):
        self._prepare("NVDA")
        self._prepare("AAPL")
        callback = dash_callback(self.app, "current-symbol-report-display.children")

        self.assertIn("NVDA", callback(1))
        self.assertIn("AAPL", callback(2))

    def test_a_stale_page_number_is_reported_rather_than_crashing(self):
        """Browser storage can hand back a page from a longer previous run."""
        self._prepare("NVDA")
        callback = dash_callback(self.app, "current-symbol-report-display.children")

        self.assertEqual(callback(99), "Invalid page")

    def test_no_symbols_shows_no_label(self):
        callback = dash_callback(self.app, "current-symbol-report-display.children")

        self.assertEqual(callback(1), "")

    def test_each_nav_button_selects_its_tab(self):
        callback = dash_callback(self.app, "tabs.active_tab")

        for trigger, expected in (
            ("nav-market", "market-analysis"),
            ("nav-social", "social-sentiment"),
            ("nav-news", "news-analysis"),
            ("nav-fundamentals", "fundamentals-analysis"),
            ("nav-researcher", "researcher-debate"),
            ("nav-research-mgr", "research-manager"),
            ("nav-trader", "trader-plan"),
            ("nav-final", "final-decision"),
        ):
            with mock.patch(
                "webui.callbacks.report_callbacks.dash.callback_context",
                mock.Mock(triggered=[{"prop_id": f"{trigger}.n_clicks"}]),
            ):
                self.assertEqual(callback(*([1] * 11)), expected, trigger)

    def test_all_three_risk_buttons_open_the_shared_debate_tab(self):
        callback = dash_callback(self.app, "tabs.active_tab")

        for trigger in ("nav-risk-agg", "nav-risk-cons", "nav-risk-neut"):
            with mock.patch(
                "webui.callbacks.report_callbacks.dash.callback_context",
                mock.Mock(triggered=[{"prop_id": f"{trigger}.n_clicks"}]),
            ):
                self.assertEqual(callback(*([1] * 11)), "risk-debate", trigger)

    def test_the_market_tab_is_the_default(self):
        callback = dash_callback(self.app, "tabs.active_tab")

        with mock.patch(
            "webui.callbacks.report_callbacks.dash.callback_context",
            mock.Mock(triggered=[]),
        ):
            self.assertEqual(callback(*([None] * 11)), "market-analysis")


class ModalCallbackTests(StateFixture):
    MODULES = ("webui.callbacks.report_callbacks",)

    def setUp(self):
        super().setUp()
        from webui.callbacks.report_callbacks import register_report_callbacks

        self.app = _app(register_report_callbacks)

    def test_nothing_triggered_leaves_the_prompt_modal_untouched(self):
        callback = dash_callback(self.app, "prompt-modal.is_open")
        state = {"is_open": False, "report_type": None}

        with mock.patch(
            "webui.callbacks.report_callbacks.ctx", mock.Mock(triggered=[])
        ):
            _open, _title, _content, returned = callback([], None, state)

        self.assertEqual(returned, state)

    def test_closing_the_prompt_modal_records_it_as_closed(self):
        callback = dash_callback(self.app, "prompt-modal.is_open")

        with mock.patch(
            "webui.callbacks.report_callbacks.ctx",
            mock.Mock(triggered=[{"prop_id": "close-prompt-modal-btn.n_clicks"}]),
        ):
            is_open, _title, _content, returned = callback([], 1, {"is_open": True})

        self.assertFalse(is_open)
        self.assertFalse(returned["is_open"])

    def test_closing_the_tool_output_modal_records_it_as_closed(self):
        callback = dash_callback(self.app, "tool-outputs-modal.is_open")

        with mock.patch(
            "webui.callbacks.report_callbacks.ctx",
            mock.Mock(triggered=[{"prop_id": "close-tool-outputs-modal-btn.n_clicks"}]),
        ):
            is_open, _title, _content, returned = callback([], 1, {"is_open": True})

        self.assertFalse(is_open)
        self.assertFalse(returned["is_open"])

    def test_the_copy_buttons_confirm_the_action(self):
        for output in ("copy-prompt-btn.children", "copy-tool-outputs-btn.children"):
            callback = dash_callback(self.app, output)

            self.assertIn("Copied!", str(callback(1)), output)

    def test_the_copy_buttons_reset_without_a_click(self):
        for output in ("copy-prompt-btn.children", "copy-tool-outputs-btn.children"):
            callback = dash_callback(self.app, output)

            self.assertIn("Copy", str(callback(None)), output)

    def test_the_export_button_confirms_the_action(self):
        callback = dash_callback(self.app, "export-tool-outputs-btn.children")

        self.assertIn("Exported!", str(callback(1)))


class ReportTabRenderingTests(StateFixture):
    """Each tab renders from whatever the run has produced so far."""

    MODULES = ("webui.callbacks.report_callbacks",)

    def setUp(self):
        super().setUp()
        from webui.callbacks.report_callbacks import register_report_callbacks

        self.app = _app(register_report_callbacks)

    def test_the_pager_says_so_when_nothing_has_run(self):
        callback = dash_callback(self.app, "report-pagination-container.children")

        self.assertIn("No symbols available", str(callback({}, 0)))

    def test_a_single_symbol_pages_without_a_count(self):
        self._prepare("NVDA")
        callback = dash_callback(self.app, "report-pagination-container.children")

        rendered = str(callback({}, 1))

        self.assertIn("NVDA", rendered)
        self.assertNotIn("Showing", rendered)

    def test_several_symbols_report_how_many(self):
        for symbol in ("NVDA", "AAPL", "MSFT"):
            self._prepare(symbol)
        callback = dash_callback(self.app, "report-pagination-container.children")

        rendered = str(callback({}, 1))

        self.assertIn("Showing 3 symbols", rendered)

    def test_the_displayed_symbol_is_the_active_button(self):
        self._prepare("NVDA")
        self._prepare("AAPL")
        self.state.current_symbol = "AAPL"
        callback = dash_callback(self.app, "report-pagination-container.children")

        rendered = str(callback({}, 1))

        self.assertIn("AAPL", rendered)

    def test_the_researcher_tab_waits_for_a_run(self):
        callback = dash_callback(self.app, "researcher-debate-tab-content.children")

        self.assertIn("No researcher debate", str(callback(None, 0)))

    def test_a_stale_page_is_reported_on_the_researcher_tab(self):
        self._prepare()
        callback = dash_callback(self.app, "researcher-debate-tab-content.children")

        self.assertIn("out of range", str(callback(99, 1)))

    def test_the_researcher_tab_renders_the_message_arrays(self):
        state = self._prepare()
        state["investment_debate_state"] = {
            "history": "some history",
            "bull_messages": ["bull opening", "bull rebuttal"],
            "bear_messages": ["bear opening"],
        }
        callback = dash_callback(self.app, "researcher-debate-tab-content.children")

        rendered = str(callback(1, 1))

        self.assertIn("bull rebuttal", rendered)
        self.assertIn("bear opening", rendered)

    def test_the_researcher_tab_falls_back_to_the_transcript(self):
        """Older runs recorded only the joined history."""
        state = self._prepare()
        state["investment_debate_state"] = {
            "history": "some history",
            "bull_history": "the bull case",
            "bear_history": "the bear case",
        }
        callback = dash_callback(self.app, "researcher-debate-tab-content.children")

        rendered = str(callback(1, 1))

        self.assertIn("the bull case", rendered)
        self.assertIn("the bear case", rendered)

    def test_an_empty_debate_says_it_has_not_begun(self):
        state = self._prepare()
        state["investment_debate_state"] = {"history": ""}
        callback = dash_callback(self.app, "researcher-debate-tab-content.children")

        self.assertIn("will begin", str(callback(1, 1)))

    def test_the_risk_tab_waits_for_a_run(self):
        callback = dash_callback(self.app, "risk-debate-tab-content.children")

        self.assertIsNotNone(callback(None, 0))

    def test_the_risk_tab_renders_each_perspective(self):
        state = self._prepare()
        state["risk_debate_state"] = {
            "history": "transcript",
            "current_risky_response": "press on",
            "current_safe_response": "trim",
            "current_neutral_response": "hold",
        }
        callback = dash_callback(self.app, "risk-debate-tab-content.children")

        rendered = str(callback(1, 1))

        self.assertIsNotNone(rendered)

    def test_every_analyst_tab_renders_its_report(self):
        """A report only surfaces once its analyst is marked completed;
        otherwise the tab keeps showing the waiting placeholder."""
        state = self._prepare()
        for key, agent in (
            ("market_report", "Market Analyst"),
            ("sentiment_report", "Social Analyst"),
            ("news_report", "News Analyst"),
            ("fundamentals_report", "Fundamentals Analyst"),
            ("macro_report", "Macro Analyst"),
        ):
            state["current_reports"][key] = f"content for {key}"
            self.state.update_agent_status(agent, "completed", symbol="NVDA")
        callback = dash_callback(self.app, "market-analysis-tab-content.children")

        rendered = str(callback(1, 1))

        self.assertIn("content for market_report", rendered)
        self.assertIn("content for macro_report", rendered)

    def test_a_report_without_a_completed_analyst_stays_pending(self):
        state = self._prepare()
        state["current_reports"]["market_report"] = "partial streaming content"
        callback = dash_callback(self.app, "market-analysis-tab-content.children")

        rendered = str(callback(1, 1))

        self.assertIn("Waiting to start", rendered)

    def test_the_tabs_render_before_any_report_exists(self):
        self._prepare()
        callback = dash_callback(self.app, "market-analysis-tab-content.children")

        self.assertIsNotNone(callback(1, 1))

    def test_the_decision_summary_reports_a_finished_run(self):
        state = self._prepare()
        state["current_reports"]["final_trade_decision"] = (
            "FINAL TRANSACTION PROPOSAL: BUY"
        )
        state["analysis_complete"] = True
        callback = dash_callback(self.app, "decision-summary.children")

        self.assertIsNotNone(callback(1, 1))


class ReportSymbolClickTests(StateFixture):
    MODULES = ("webui.callbacks.report_callbacks",)

    def setUp(self):
        super().setUp()
        from webui.callbacks.report_callbacks import register_report_callbacks

        self.app = _app(register_report_callbacks)
        for symbol in ("NVDA", "AAPL", "MSFT"):
            self._prepare(symbol)
        self.state.current_symbol = "NVDA"
        self.callback = dash_callback(self.app, "report-pagination.active_page")

    def _click(self, index, clicks=None):
        prop = '{"component":"reports","index":%d,"type":"symbol-btn"}.n_clicks' % index
        with mock.patch(
            "webui.callbacks.report_callbacks.ctx",
            mock.Mock(triggered=[{"prop_id": prop}]),
        ):
            return self.callback(clicks or [0, 1, 0])

    def test_clicking_a_symbol_selects_it_everywhere(self):
        """Reports and the chart page together."""
        report_page, chart_page, _buttons = self._click(1)

        self.assertEqual(report_page, 2)
        self.assertEqual(chart_page, 2)
        self.assertEqual(self.state.current_symbol, "AAPL")

    def test_the_clicked_button_becomes_the_active_one(self):
        _report, _chart, buttons = self._click(1)

        self.assertIn("AAPL", str(buttons))

    def test_several_symbols_keep_their_count(self):
        _report, _chart, buttons = self._click(1)

        self.assertIn("Showing 3 symbols", str(buttons))

    def test_an_out_of_range_index_changes_nothing(self):
        before = self.state.current_symbol

        self._click(99)

        self.assertEqual(self.state.current_symbol, before)

    def test_no_click_changes_nothing(self):
        result = self._click(1, clicks=[0, 0, 0])

        self.assertTrue(all(item is dash.no_update for item in result))


class PromptModalOpenTests(StateFixture):
    # The prompt lookup resolves through prompt_capture's own app_state.
    MODULES = (
        "webui.callbacks.report_callbacks",
        "webui.utils.prompt_capture",
    )

    def setUp(self):
        super().setUp()
        from webui.callbacks.report_callbacks import register_report_callbacks

        self.app = _app(register_report_callbacks)
        self._prepare("NVDA")

    def test_opening_a_prompt_shows_the_captured_text(self):
        self.state.store_agent_prompt(
            "market_report", "You are a market analyst.", symbol="NVDA"
        )
        callback = dash_callback(self.app, "prompt-modal.is_open")
        prop = '{"report":"market_report","type":"show-prompt-btn"}.n_clicks'

        with mock.patch(
            "webui.callbacks.report_callbacks.ctx",
            mock.Mock(triggered=[{"prop_id": prop}]),
        ):
            is_open, title, content, state = callback([1], None, {"is_open": False})

        self.assertTrue(is_open)
        self.assertIn("You are a market analyst.", str(content))
        self.assertTrue(state["is_open"])

    def test_opening_a_tool_output_view_shows_the_recorded_calls(self):
        self.state.tool_calls_log.append(
            {
                "timestamp": "10:00:00",
                "tool_name": "get_market_data_report",
                "inputs": {"symbol": "NVDA"},
                "output": "bars",
                "status": "success",
                "agent_type": "Market Analyst",
                "symbol": "NVDA",
            }
        )
        callback = dash_callback(self.app, "tool-outputs-modal.is_open")
        prop = '{"report":"market_report","type":"show-tool-outputs-btn"}.n_clicks'

        with mock.patch(
            "webui.callbacks.report_callbacks.ctx",
            mock.Mock(triggered=[{"prop_id": prop}]),
        ):
            is_open, title, content, state = callback([1], None, {"is_open": False})

        self.assertTrue(is_open)
        self.assertIn("get_market_data_report", str(content))
