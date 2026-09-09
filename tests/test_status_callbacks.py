"""Tests for the run-status panel and the refresh governor.

The agent status table is how an operator sees where a run is, and the
refresh governor decides whether the UI is polling at all. Leaving fast
refresh armed after a run burns CPU forever; disarming it too early leaves
the last agent showing as in-progress with no way to notice it finished.
"""

from __future__ import annotations

import unittest
from unittest import mock

import dash

from conftest import dash_callback
from webui.callbacks import status_callbacks
from webui.utils.state import AppState


class StatusFixture(unittest.TestCase):
    def setUp(self):
        app = dash.Dash(__name__, suppress_callback_exceptions=True)
        status_callbacks.register_status_callbacks(app)
        self.app = app

        self.state = AppState()
        patcher = mock.patch.object(status_callbacks, "app_state", self.state)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, symbol="NVDA", statuses=None):
        self.state.init_symbol_state(symbol)
        self.state.current_symbol = symbol
        if statuses:
            self.state.get_state(symbol)["agent_statuses"].update(statuses)


class StatusTableTests(StatusFixture):
    def _table(self):
        return str(dash_callback(self.app, "status-table.children")(0, 0))

    def test_no_run_renders_an_empty_table(self):
        self.assertIsNotNone(self._table())

    def test_the_downstream_teams_are_always_listed(self):
        self._run()

        rendered = self._table()

        for agent in ("Bull Researcher", "Research Manager", "Trader", "Portfolio Manager"):
            self.assertIn(agent, rendered, agent)

    def test_only_the_selected_analysts_are_listed(self):
        """An unselected analyst never runs, so showing it as pending lies."""
        self._run()
        self.state.active_analysts = ["Market Analyst"]

        rendered = self._table()

        self.assertIn("Market Analyst", rendered)
        self.assertNotIn("Social Analyst", rendered)

    def test_the_analyst_team_is_omitted_when_none_are_selected(self):
        self._run()
        self.state.active_analysts = []

        self.assertNotIn("Analyst Team", self._table())

    def test_each_status_is_shown_distinctly(self):
        self._run(statuses={"Trader": "completed", "Bull Researcher": "in_progress"})

        rendered = self._table()

        self.assertIn("COMPLETED", rendered)
        self.assertIn("IN PROGRESS", rendered)
        self.assertIn("PENDING", rendered)

    def test_an_unknown_agent_reads_as_pending(self):
        self._run()

        self.assertIn("PENDING", self._table())


class ProgressStatTests(StatusFixture):
    def test_the_counters_are_rendered(self):
        self.state.tool_calls_count = 7
        self.state.llm_calls_count = 12
        self.state.generated_reports_count = 3

        tools, llms, reports = dash_callback(self.app, "tool-calls-text.children")(0)

        self.assertIn("7", tools)
        self.assertIn("12", llms)
        self.assertIn("3", reports)


class RefreshGovernorTests(StatusFixture):
    def _manage(self):
        return dash_callback(self.app, "refresh-interval.disabled")({}, 0)

    def test_fast_refresh_is_armed_while_a_run_is_in_progress(self):
        self.state.analysis_running = True

        fast_disabled, _medium, message, className = self._manage()

        self.assertFalse(fast_disabled)
        self.assertIn("Auto-refreshing", message)
        self.assertIn("text-success", className)

    def test_fast_refresh_is_disarmed_when_nothing_is_running(self):
        """Otherwise the browser polls once a second forever."""
        fast_disabled, _medium, message, className = self._manage()

        self.assertTrue(fast_disabled)
        self.assertIn("paused", message)
        self.assertIn("text-secondary", className)

    def test_a_pending_ui_update_arms_one_more_cycle(self):
        """The last agent's result arrives after the thread has ended."""
        self.state.needs_ui_update = True

        fast_disabled, _medium, message, _class = self._manage()

        self.assertFalse(fast_disabled)
        self.assertIn("Finalizing", message)

    def test_the_pending_flag_is_cleared_once_signalled(self):
        self.state.needs_ui_update = True

        self._manage()

        self.assertFalse(self.state.needs_ui_update)
        self.assertTrue(self._manage()[0])

    def test_the_medium_interval_stays_armed_so_late_results_render(self):
        _fast, medium_disabled, _message, _class = self._manage()

        self.assertFalse(medium_disabled)

    def test_loop_mode_says_it_is_waiting_between_iterations(self):
        self.state.loop_enabled = True
        self.state.loop_interval_minutes = 15

        _fast, _medium, message, className = self._manage()

        self.assertIn("Loop mode", message)
        self.assertIn("15 min", message)
        self.assertIn("text-info", className)

    def test_loop_mode_says_so_while_analyzing(self):
        self.state.loop_enabled = True
        self.state.analysis_running = True

        _fast, _medium, message, className = self._manage()

        self.assertIn("Loop mode active", message)
        self.assertIn("text-warning", className)

    def test_market_hour_mode_says_so_while_analyzing(self):
        self.state.market_hour_enabled = True
        self.state.analysis_running = True

        _fast, _medium, message, _class = self._manage()

        self.assertIn("Market hour mode", message)
        self.assertIn("in progress", message)

    def test_market_hour_mode_names_the_next_execution(self):
        import datetime

        self.state.market_hour_enabled = True
        self.state.market_hours = [9, 15]

        with mock.patch(
            "webui.utils.market_hours.get_next_market_datetime",
            lambda hour: datetime.datetime(2026, 9, 10, hour, 0),
        ):
            _fast, _medium, message, _class = self._manage()

        self.assertIn("Next:", message)
        self.assertIn("Thursday", message)

    def test_only_the_first_two_hours_are_shown(self):
        import datetime

        self.state.market_hour_enabled = True
        self.state.market_hours = [9, 12, 15]

        with mock.patch(
            "webui.utils.market_hours.get_next_market_datetime",
            lambda hour: datetime.datetime(2026, 9, 10, hour, 0),
        ):
            _fast, _medium, message, _class = self._manage()

        self.assertEqual(message.count("→"), 2)

    def test_an_unschedulable_hour_falls_back_to_a_plain_message(self):
        self.state.market_hour_enabled = True
        self.state.market_hours = [9]

        with mock.patch(
            "webui.utils.market_hours.get_next_market_datetime",
            mock.Mock(side_effect=RuntimeError("no timezone data")),
        ):
            _fast, _medium, message, _class = self._manage()

        self.assertIn("Waiting for next market hour", message)


if __name__ == "__main__":
    unittest.main()
