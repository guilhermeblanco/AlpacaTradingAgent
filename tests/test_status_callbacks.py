"""Tests for the per-session counters.

The agent status table moved to the pipeline board and the refresh governor
was retired in favour of the server-sent pulse; what is left here is the
tool/LLM/report tally for the current session.
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


class ProgressStatTests(StatusFixture):
    def test_the_counters_are_rendered(self):
        self.state.tool_calls_count = 7
        self.state.llm_calls_count = 12
        self.state.generated_reports_count = 3

        tools, llms, reports = dash_callback(self.app, "tool-calls-text.children")(0)

        self.assertIn("7", tools)
        self.assertIn("12", llms)
        self.assertIn("3", reports)


if __name__ == "__main__":
    unittest.main()
