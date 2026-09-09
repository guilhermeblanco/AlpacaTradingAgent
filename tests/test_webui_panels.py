"""Tests for the account, cost, and chart panels.

These are the read-only surfaces an operator watches: broker positions and
orders, what the run is costing against what it returned, and the price
chart. They must render from partial or broken upstream data rather than
blanking the page.
"""

from __future__ import annotations

import unittest
from unittest import mock

import pandas as pd

from webui.callbacks import cost_callbacks as cc
from webui.components import alpaca_account as aa
from webui.utils import charts


class OrderPaginationWindowTests(unittest.TestCase):
    """Order history can run to hundreds of pages; the bar stays compact."""

    def test_a_short_history_lists_every_page(self):
        self.assertEqual(aa._visible_order_pages(1, 5), [1, 2, 3, 4, 5])

    def test_eleven_pages_still_fit_without_a_gap(self):
        self.assertEqual(aa._visible_order_pages(6, 11), list(range(1, 12)))

    def test_a_long_history_collapses_the_middle(self):
        visible = aa._visible_order_pages(1, 50)

        self.assertIn("gap", visible)
        self.assertLess(len(visible), 50)

    def test_the_newest_and_oldest_pages_are_always_reachable(self):
        visible = aa._visible_order_pages(25, 50)

        self.assertIn(1, visible)
        self.assertIn(50, visible)

    def test_the_active_page_is_always_reachable(self):
        visible = aa._visible_order_pages(25, 50)

        self.assertIn(25, visible)

    def test_pages_are_listed_in_order(self):
        pages = [p for p in aa._visible_order_pages(25, 50) if p != "gap"]

        self.assertEqual(pages, sorted(pages))

    def test_an_active_page_inside_a_window_adds_no_gap_around_itself(self):
        visible = aa._visible_order_pages(2, 50)

        self.assertEqual(visible.count("gap"), 1)


class OrderPaginationRenderTests(unittest.TestCase):
    def test_the_control_renders_for_a_single_page(self):
        self.assertIsNotNone(aa.render_orders_pagination(1, 1, total_orders=3))

    def test_an_out_of_range_page_is_clamped(self):
        """A stale page number from browser storage must not blank the list."""
        self.assertIsNotNone(aa.render_orders_pagination(99, 3, total_orders=10))

    def test_a_zero_page_count_is_treated_as_one(self):
        self.assertIsNotNone(aa.render_orders_pagination(1, 0))

    def test_none_values_do_not_raise(self):
        self.assertIsNotNone(aa.render_orders_pagination(None, None))


class OrderTableTests(unittest.TestCase):
    # Exactly the shape AlpacaUtils.get_recent_orders_page produces.
    ORDER = {
        "Asset": "NVDA",
        "Side": "buy",
        "Status": "filled",
        "Order Type": "market",
        "Avg. Fill Price": "$123.45",
        "Qty": "10",
        "Filled Qty": "10",
        "Source": "alpaca",
    }

    def test_orders_are_rendered(self):
        rendered = str(aa.render_orders_table_body([self.ORDER]))

        self.assertIn("NVDA", rendered)

    def test_an_empty_page_renders_an_empty_state(self):
        self.assertIsNotNone(aa.render_orders_table_body([], page=1))

    def test_the_renderer_and_the_producer_agree_on_keys(self):
        """render_orders_table_body indexes these directly, so a rename in
        AlpacaUtils.get_recent_orders_page would raise mid-render."""
        import inspect

        from tradingagents.dataflows.alpaca_utils import AlpacaUtils

        producer = inspect.getsource(AlpacaUtils.get_recent_orders_page)
        for key in self.ORDER:
            self.assertIn(f'"{key}"', producer, key)

        self.assertIsNotNone(aa.render_orders_table_body([self.ORDER]))

    def test_several_orders_render_together(self):
        second = dict(self.ORDER, Asset="AAPL")

        rendered = str(aa.render_orders_table_body([self.ORDER, second]))

        self.assertIn("NVDA", rendered)
        self.assertIn("AAPL", rendered)

    def test_the_error_state_names_the_problem(self):
        rendered = str(aa.render_orders_table_error(RuntimeError("credentials")))

        self.assertIn("credentials", rendered)

    def test_the_shell_declares_the_containers_the_refresh_fills(self):
        rendered = str(aa.render_orders_table_shell())

        self.assertIn("orders-table-body-container", rendered)
        self.assertIn("orders-pagination-container", rendered)


class PositionsAndSummaryTests(unittest.TestCase):
    def test_positions_render_from_broker_rows(self):
        # Exactly the shape AlpacaUtils.get_positions_data produces.
        rows = [
            {
                "Symbol": "NVDA",
                "Qty": 10.0,
                "Current Price": "$123.45",
                "Avg Entry": "$113.00",
                "Market Value": "$1234.50",
                "Cost Basis": "$1130.00",
                "Virtual Stop Loss": "-",
                "Virtual Take Profit": "-",
                "Today's P/L (%)": "2.10%",
                "Today's P/L ($)": "$25.00",
                "Total P/L (%)": "9.20%",
                "Total P/L ($)": "$104.50",
            }
        ]

        with mock.patch.object(aa.AlpacaUtils, "get_positions_data", lambda: rows):
            self.assertIn("NVDA", str(aa.render_positions_table()))

    def test_no_positions_renders_an_empty_state(self):
        with mock.patch.object(aa.AlpacaUtils, "get_positions_data", lambda: []):
            self.assertIsNotNone(aa.render_positions_table())

    def test_a_broker_failure_renders_rather_than_raising(self):
        def explode():
            raise RuntimeError("credentials missing")

        with mock.patch.object(aa.AlpacaUtils, "get_positions_data", explode):
            self.assertIsNotNone(aa.render_positions_table())

    def test_the_account_summary_renders_from_broker_values(self):
        info = {
            "buying_power": 50_000.0,
            "cash": 25_000.0,
            "daily_change_dollars": 250.0,
            "daily_change_percent": 1.2,
            "portfolio_value": 100_000.0,
        }

        with mock.patch.object(aa.AlpacaUtils, "get_account_info", lambda: info):
            self.assertIsNotNone(aa.render_account_summary())

    def test_an_account_failure_renders_rather_than_raising(self):
        def explode():
            raise RuntimeError("no credentials")

        with mock.patch.object(aa.AlpacaUtils, "get_account_info", explode):
            self.assertIsNotNone(aa.render_account_summary())


class CostFormattingTests(unittest.TestCase):
    def test_money_is_formatted_with_thousands_separators(self):
        self.assertEqual(cc._fmt_usd(1234.5), "$1,234.50")

    def test_an_unknown_cost_is_shown_as_a_dash(self):
        """Zero and unpriced are different things."""
        self.assertEqual(cc._fmt_usd(None), "—")
        self.assertEqual(cc._fmt_usd(0), "$0.00")

    def test_tokens_are_formatted_as_whole_numbers(self):
        self.assertEqual(cc._fmt_tokens(1234567), "1,234,567")
        self.assertEqual(cc._fmt_tokens(None), "0")

    def test_a_summary_card_renders_its_label_and_value(self):
        rendered = str(cc._summary_card("Total cost", "$12.34"))

        self.assertIn("Total cost", rendered)
        self.assertIn("$12.34", rendered)


class BudgetTextTests(unittest.TestCase):
    def test_a_configured_budget_is_shown_as_a_fraction(self):
        guard = mock.MagicMock()
        guard.llm_tokens_used.return_value = 1000
        guard.config = {"daily_llm_token_budget": 5000}

        with mock.patch("tradingagents.safety.get_safety_guard", lambda: guard):
            text, _color = cc._budget_text()

        self.assertIn("1,000", text)
        self.assertIn("5,000", text)

    def test_an_unlimited_budget_is_shown_as_infinite(self):
        guard = mock.MagicMock()
        guard.llm_tokens_used.return_value = 1000
        guard.config = {"daily_llm_token_budget": 0}

        with mock.patch("tradingagents.safety.get_safety_guard", lambda: guard):
            text, _color = cc._budget_text()

        self.assertIn("∞", text)

    def test_an_exhausted_budget_is_coloured_as_an_error(self):
        guard = mock.MagicMock()
        guard.llm_tokens_used.return_value = 5000
        guard.config = {"daily_llm_token_budget": 5000}

        with mock.patch("tradingagents.safety.get_safety_guard", lambda: guard):
            _text, color = cc._budget_text()

        self.assertEqual(color, cc.COLORS["error"])

    def test_an_unavailable_guard_degrades_to_a_dash(self):
        with mock.patch(
            "tradingagents.safety.get_safety_guard", side_effect=RuntimeError("no db")
        ):
            text, _color = cc._budget_text()

        self.assertEqual(text, "—")


class CostTableTests(unittest.TestCase):
    PER_SYMBOL = {
        "NVDA": {"runs": 3, "total_tokens": 10_000, "cost_usd": 1.25},
        "AAPL": {"runs": 1, "total_tokens": 2_000, "cost_usd": 0.25},
    }

    def test_no_data_renders_nothing(self):
        self.assertIsNone(cc._build_symbol_table({}, {}))
        self.assertIsNone(cc._build_model_table({}))

    def test_symbols_are_ordered_by_spend(self):
        rendered = str(cc._build_symbol_table(self.PER_SYMBOL, {}))

        self.assertLess(rendered.index("NVDA"), rendered.index("AAPL"))

    def test_a_symbol_without_resolved_outcomes_shows_a_dash(self):
        rendered = str(cc._build_symbol_table(self.PER_SYMBOL, {}))

        self.assertIn("—", rendered)

    def test_a_realized_return_is_shown_with_its_sign(self):
        returns = {"NVDA": {"avg_return": 0.0421, "resolved": 3}}

        rendered = str(cc._build_symbol_table(self.PER_SYMBOL, returns))

        self.assertIn("+4.21%", rendered)

    def test_a_negative_return_is_coloured_as_an_error(self):
        returns = {"NVDA": {"avg_return": -0.05, "resolved": 2}}

        rendered = str(cc._build_symbol_table(self.PER_SYMBOL, returns))

        self.assertIn(cc.COLORS["error"], rendered)

    def test_models_are_ordered_by_spend(self):
        per_model = {
            "gpt-5.4-nano": {"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.10},
            "gpt-5.4-mini": {"input_tokens": 900, "output_tokens": 400, "cost_usd": 1.10},
        }

        rendered = str(cc._build_model_table(per_model))

        self.assertLess(rendered.index("gpt-5.4-mini"), rendered.index("gpt-5.4-nano"))

    def test_an_unpriced_model_is_called_out(self):
        """Otherwise the total silently understates the real spend."""
        per_model = {
            "some-new-model": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cost_usd": 0.0,
                "unpriced": True,
            }
        }

        self.assertIn("unpriced", str(cc._build_model_table(per_model)))

    def test_the_daily_figure_is_built_from_the_series(self):
        figure = cc._build_daily_figure(
            {"2026-09-08": {"cost_usd": 1.0}, "2026-09-09": {"cost_usd": 2.0}}
        )

        self.assertIsNotNone(figure)

    def test_the_daily_figure_handles_no_days(self):
        self.assertIsNotNone(cc._build_daily_figure({}))


class ChartTests(unittest.TestCase):
    def test_the_welcome_chart_is_always_available(self):
        self.assertIsNotNone(charts.create_welcome_chart())

    def test_the_demo_chart_renders_without_a_provider(self):
        self.assertIsNotNone(charts.create_demo_chart("NVDA"))

    def test_the_demo_chart_can_carry_an_error_message(self):
        figure = charts.create_demo_chart("NVDA", error_msg="provider down")

        self.assertIsNotNone(figure)

    def test_a_chart_is_built_from_provider_bars(self):
        bars = pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-01-01", periods=30, freq="D"),
                "open": range(100, 130),
                "high": range(101, 131),
                "low": range(99, 129),
                "close": range(100, 130),
                "volume": [1_000] * 30,
            }
        )

        with mock.patch.object(charts.AlpacaUtils, "get_stock_data", lambda *a, **k: bars):
            self.assertIsNotNone(charts.create_chart("NVDA", period="1mo"))

    def test_a_provider_failure_falls_back_to_a_demo_chart(self):
        def explode(*_a, **_k):
            raise RuntimeError("provider down")

        with mock.patch.object(charts.AlpacaUtils, "get_stock_data", explode):
            self.assertIsNotNone(charts.create_chart("NVDA"))

    def test_empty_provider_data_falls_back(self):
        with mock.patch.object(
            charts.AlpacaUtils, "get_stock_data", lambda *a, **k: pd.DataFrame()
        ):
            self.assertIsNotNone(charts.create_chart("NVDA"))

    def test_the_screener_overview_renders(self):
        with mock.patch(
            "tradingagents.screener.get_scan_status",
            lambda: {"candidates_found": [], "is_running": False},
        ):
            self.assertIsNotNone(charts.create_screener_overview_chart())


if __name__ == "__main__":
    unittest.main()
