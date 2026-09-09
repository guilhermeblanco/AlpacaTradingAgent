"""Tests for the chart panel's callbacks.

The chart pane is per-symbol and paginated, so two things have to stay in
step: which symbol the buttons say is selected, and which symbol the figure
is actually drawn for. A failure to fetch bars must render the welcome
chart with the symbol named, not an empty pane.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

import dash

from conftest import dash_callback
from webui.callbacks import chart_callbacks
from webui.utils.state import AppState


class ChartFixture(unittest.TestCase):
    def setUp(self):
        app = dash.Dash(__name__, suppress_callback_exceptions=True)
        chart_callbacks.register_chart_callbacks(app)
        self.app = app

        self.state = AppState()
        patcher = mock.patch.object(chart_callbacks, "app_state", self.state)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _symbols(self, *symbols, current=None):
        for symbol in symbols:
            self.state.init_symbol_state(symbol)
        self.state.current_symbol = current or (symbols[0] if symbols else None)

    def _context(self, prop_id=None, triggered_id=None):
        context = SimpleNamespace(
            triggered=[{"prop_id": prop_id}] if prop_id else [],
            triggered_id=triggered_id,
        )
        return mock.patch.object(chart_callbacks, "ctx", context)


class SymbolPaginationTests(ChartFixture):
    def _render(self):
        return dash_callback(self.app, "chart-pagination-container.children")({}, 0)

    def test_a_button_is_rendered_for_each_symbol(self):
        self._symbols("NVDA", "AAPL")

        rendered = str(self._render())

        self.assertIn("NVDA", rendered)
        self.assertIn("AAPL", rendered)

    def test_the_current_symbol_is_the_active_button(self):
        self._symbols("NVDA", "AAPL", current="AAPL")

        rendered = str(self._render())

        self.assertIn("'AAPL'", rendered)
        self.assertIn("active", rendered)

    def test_several_symbols_get_a_count_line(self):
        self._symbols("NVDA", "AAPL")

        self.assertIn("Charts for 2 symbols", str(self._render()))

    def test_a_single_symbol_gets_no_count_line(self):
        self._symbols("NVDA")

        self.assertNotIn("Charts for", str(self._render()))

    def test_no_symbols_says_so(self):
        self.assertIn("No symbols available", str(self._render()))

    def test_an_unknown_current_symbol_falls_back_to_the_first(self):
        self._symbols("NVDA", "AAPL", current="GONE")

        self.assertIn("NVDA", str(self._render()))


class SymbolClickTests(ChartFixture):
    def _click(self, clicks, index=1):
        prop_id = (
            '{"component":"charts","index":%d,"type":"symbol-btn"}.n_clicks' % index
        )
        with self._context(prop_id=prop_id):
            return dash_callback(self.app, "chart-pagination.active_page")(clicks)

    def test_clicking_a_symbol_selects_its_page(self):
        self._symbols("NVDA", "AAPL")

        chart_page, report_page, _buttons = self._click([0, 1], index=1)

        self.assertEqual((chart_page, report_page), (2, 2))

    def test_the_chart_and_report_panes_move_together(self):
        """Otherwise the report shown belongs to a different symbol."""
        self._symbols("NVDA", "AAPL", current="NVDA")

        chart_page, report_page, _buttons = self._click([0, 1], index=1)

        self.assertEqual(chart_page, report_page)
        self.assertEqual(self.state.current_symbol, "AAPL")

    def test_the_buttons_update_without_waiting_for_a_refresh(self):
        self._symbols("NVDA", "AAPL")

        _chart, _report, buttons = self._click([0, 1], index=1)

        self.assertIn("AAPL", str(buttons))

    def test_a_single_symbol_click_renders_without_a_count_line(self):
        self._symbols("NVDA")

        _chart, _report, buttons = self._click([1], index=0)

        self.assertNotIn("Charts for", str(buttons))

    def test_no_click_changes_nothing(self):
        self._symbols("NVDA", "AAPL")

        result = self._click([0, 0])

        self.assertTrue(all(value is dash.no_update for value in result))

    def test_a_click_on_a_symbol_that_no_longer_exists_changes_nothing(self):
        self._symbols("NVDA")

        result = self._click([0, 1], index=5)

        self.assertTrue(all(value is dash.no_update for value in result))


class ChartRenderTests(ChartFixture):
    def _update(self, active_page=1, store=None, prop_id=None, figure="the chart",
                error=None):
        def create_chart(symbol, period):
            if error:
                raise error
            return f"{figure}:{symbol}:{period}"

        with mock.patch.object(chart_callbacks, "create_chart", create_chart):
            with mock.patch.object(
                chart_callbacks, "create_welcome_chart", lambda: "welcome"
            ):
                with self._context(prop_id=prop_id):
                    return dash_callback(self.app, "chart-container.figure")(
                        0, 0, 0, 0, active_page, 0, store
                    )

    def test_the_chart_is_drawn_for_the_paged_symbol(self):
        self._symbols("NVDA", "AAPL")

        figure, display, _store = self._update(active_page=2)

        self.assertIn("AAPL", figure)
        self.assertIn("AAPL", display)

    def test_no_symbols_shows_the_welcome_chart(self):
        figure, display, _store = self._update()

        self.assertEqual(figure, "welcome")
        self.assertEqual(display, "")

    def test_a_page_beyond_the_symbol_list_says_so(self):
        """A refresh can leave the pager pointing past the end."""
        self._symbols("NVDA")

        figure, display, _store = self._update(active_page=5)

        self.assertEqual(figure, "welcome")
        self.assertIn("out of range", display)

    def test_a_period_button_selects_its_period(self):
        self._symbols("NVDA")

        for prop_id, period in (
            ("period-1d.n_clicks", "1d"),
            ("period-1w.n_clicks", "1w"),
            ("period-1mo.n_clicks", "1mo"),
            ("period-1y.n_clicks", "1y"),
        ):
            figure, _display, store = self._update(prop_id=prop_id)

            self.assertIn(f":{period}", figure)
            self.assertEqual(store["selected_period"], period)

    def test_the_stored_period_survives_a_symbol_change(self):
        self._symbols("NVDA", "AAPL")

        figure, _display, _store = self._update(
            active_page=2, store={"selected_period": "1mo"}
        )

        self.assertIn(":1mo", figure)

    def test_the_default_period_is_a_year(self):
        self._symbols("NVDA")

        figure, _display, _store = self._update()

        self.assertIn(":1y", figure)

    def test_the_store_records_what_was_drawn(self):
        self._symbols("NVDA")

        _figure, _display, store = self._update()

        self.assertEqual(store["last_symbol"], "NVDA")
        self.assertIn("last_updated", store)

    def test_a_failing_chart_names_the_symbol_rather_than_going_blank(self):
        self._symbols("NVDA")

        figure, display, _store = self._update(error=RuntimeError("no bars"))

        self.assertEqual(figure, "welcome")
        self.assertIn("NVDA", display)


class ChartTimestampTests(ChartFixture):
    def _timestamp(self, store):
        return dash_callback(self.app, "chart-last-updated.children")(store)

    def test_the_draw_time_is_rendered(self):
        rendered = self._timestamp({"last_updated": "2026-09-09T14:30:00"})

        self.assertIn("Last updated:", rendered)
        self.assertIn("02:30:00 PM", rendered)

    def test_no_store_shows_nothing(self):
        self.assertEqual(self._timestamp(None), "")
        self.assertEqual(self._timestamp({}), "")

    def test_an_unparseable_timestamp_shows_nothing(self):
        self.assertEqual(self._timestamp({"last_updated": "whenever"}), "")


class PeriodButtonTests(ChartFixture):
    def _active(self, triggered_id):
        with self._context(triggered_id=triggered_id):
            return dash_callback(self.app, "period-1d.active")(0, 0, 0, 0)

    def test_the_clicked_period_is_the_active_one(self):
        self.assertEqual(self._active("period-1w"), (False, True, False, False))

    def test_a_year_is_active_before_anything_is_clicked(self):
        self.assertEqual(self._active(None), (False, False, False, True))

    def test_exactly_one_period_is_ever_active(self):
        for button in ("period-1d", "period-1w", "period-1mo", "period-1y"):
            self.assertEqual(sum(self._active(button)), 1, button)


if __name__ == "__main__":
    unittest.main()
