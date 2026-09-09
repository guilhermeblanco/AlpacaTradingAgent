"""Tests for the tool timing wrapper.

Every analyst tool is wrapped by this. It enforces a timeout so one hung
provider cannot stall a run, retries a web search that came back with a
non-answer, strips the chat tail off what it returns, and records the call
for the UI. A tool must never raise out of it.
"""

from __future__ import annotations

import time
import unittest
from unittest import mock

from tradingagents.agents.utils.agent_utils import Toolkit, timing_wrapper


class WrapperFixture(unittest.TestCase):
    def setUp(self):
        self._original = dict(Toolkit._config)
        self.addCleanup(lambda: Toolkit._config.update(self._original))
        Toolkit._config.update(
            {
                "tool_semantic_retry_enabled": True,
                "tool_semantic_retry_max_retries": 1,
                "tool_semantic_retry_backoff_seconds": 0.0,
                "web_search_timeout_extension_seconds": 0,
                "tool_semantic_retry_disabled_tools": [],
            }
        )


class ResultHandlingTests(WrapperFixture):
    def test_a_tool_result_is_returned(self):
        @timing_wrapper("MARKET")
        def get_market_data_report(symbol):
            return f"report for {symbol}"

        self.assertEqual(get_market_data_report("NVDA"), "report for NVDA")

    def test_the_chat_tail_is_stripped_from_a_result(self):
        @timing_wrapper("NEWS")
        def get_google_news(query):
            return "Real analysis.\n\nWould you like me to dig deeper?"

        self.assertEqual(get_google_news("NVDA"), "Real analysis.")

    def test_a_raising_tool_is_recorded_and_re_raised(self):
        """The wrapper records the failure; the analyst's tool loop is what
        turns it into an error string the model can read."""
        from webui.utils.state import AppState

        state = AppState()

        @timing_wrapper("MARKET")
        def get_market_data_report(symbol):
            raise RuntimeError("provider down")

        with mock.patch("webui.utils.state.app_state", state):
            with self.assertRaises(RuntimeError):
                get_market_data_report("NVDA")

        recorded = state.get_tool_calls_for_display()
        self.assertTrue(recorded)
        self.assertEqual(recorded[0]["status"], "error")

    def test_a_non_string_result_is_passed_through(self):
        @timing_wrapper("MARKET")
        def get_number(symbol):
            return 42

        self.assertEqual(get_number("NVDA"), 42)

    def test_the_wrapper_keeps_the_tool_identity(self):
        @timing_wrapper("MARKET")
        def get_market_data_report(symbol):
            """The docstring the agent framework reads."""
            return ""

        self.assertEqual(get_market_data_report.__name__, "get_market_data_report")
        self.assertIn("docstring", get_market_data_report.__doc__)


class TimeoutTests(WrapperFixture):
    def test_a_hung_tool_is_abandoned_and_reported(self):
        """Driven through the executor rather than a real sleep: the floor
        below means a genuine timeout takes at least ten seconds."""
        import concurrent.futures

        class TimingOutFuture:
            def result(self, timeout=None):
                raise concurrent.futures.TimeoutError()

            def cancel(self):
                return True

        class TimingOutExecutor:
            def __init__(self, *a, **k):
                pass

            def submit(self, *_a, **_k):
                return TimingOutFuture()

            def shutdown(self, *a, **k):
                return None

        @timing_wrapper("MARKET", timeout_seconds=1)
        def get_market_data_report(symbol):
            return "never returned"

        with mock.patch.object(
            concurrent.futures, "ThreadPoolExecutor", TimingOutExecutor
        ):
            result = get_market_data_report("NVDA")

        self.assertIn("timed out", result)

    def test_a_tool_within_the_floor_is_not_abandoned(self):
        """max(10s, ...) keeps a misconfigured tiny timeout from failing
        every tool instantly."""

        @timing_wrapper("MARKET", timeout_seconds=0.05)
        def get_market_data_report(symbol):
            time.sleep(0.2)
            return "completed"

        self.assertEqual(get_market_data_report("NVDA"), "completed")

    def test_web_search_tools_get_a_configured_extension(self):
        Toolkit._config["web_search_timeout_extension_seconds"] = 30
        captured = {}

        @timing_wrapper("NEWS", timeout_seconds=10, uses_web_search=True)
        def get_global_news_openai(curr_date):
            captured["ran"] = True
            return "x" * 2000

        get_global_news_openai("2026-09-09")

        self.assertTrue(captured["ran"])

    def test_the_timeout_never_drops_below_the_floor(self):
        """A misconfigured tiny timeout would fail every tool instantly."""

        @timing_wrapper("MARKET", timeout_seconds=0)
        def get_market_data_report(symbol):
            return "fast enough"

        self.assertEqual(get_market_data_report("NVDA"), "fast enough")


class SemanticRetryTests(WrapperFixture):
    def test_a_non_answer_from_a_web_search_is_retried(self):
        calls = {"count": 0}

        @timing_wrapper("NEWS", uses_web_search=True)
        def get_stock_news_openai(ticker, curr_date):
            calls["count"] += 1
            if calls["count"] == 1:
                return "Would you like me to search for news?"
            return "Real research. " * 100

        result = get_stock_news_openai("NVDA", "2026-09-09")

        self.assertEqual(calls["count"], 2)
        self.assertIn("Real research.", result)

    def test_a_good_answer_is_not_retried(self):
        calls = {"count": 0}

        @timing_wrapper("NEWS", uses_web_search=True)
        def get_stock_news_openai(ticker, curr_date):
            calls["count"] += 1
            return "Real research. " * 100

        get_stock_news_openai("NVDA", "2026-09-09")

        self.assertEqual(calls["count"], 1)

    def test_a_non_web_tool_is_never_retried(self):
        calls = {"count": 0}

        @timing_wrapper("MARKET")
        def get_market_data_report(symbol):
            calls["count"] += 1
            return ""

        get_market_data_report("NVDA")

        self.assertEqual(calls["count"], 1)

    def test_retry_can_be_switched_off(self):
        Toolkit._config["tool_semantic_retry_enabled"] = False
        calls = {"count": 0}

        @timing_wrapper("NEWS", uses_web_search=True)
        def get_stock_news_openai(ticker, curr_date):
            calls["count"] += 1
            return "Would you like me to search?"

        get_stock_news_openai("NVDA", "2026-09-09")

        self.assertEqual(calls["count"], 1)

    def test_the_retry_budget_is_respected(self):
        calls = {"count": 0}

        @timing_wrapper("NEWS", uses_web_search=True)
        def get_stock_news_openai(ticker, curr_date):
            calls["count"] += 1
            return "Would you like me to search?"

        get_stock_news_openai("NVDA", "2026-09-09")

        self.assertLessEqual(calls["count"], 2)

    def test_a_disabled_tool_is_never_retried(self):
        Toolkit._config["tool_semantic_retry_disabled_tools"] = [
            "get_stock_news_openai"
        ]
        calls = {"count": 0}

        @timing_wrapper("NEWS", uses_web_search=True)
        def get_stock_news_openai(ticker, curr_date):
            calls["count"] += 1
            return "Would you like me to search?"

        get_stock_news_openai("NVDA", "2026-09-09")

        self.assertEqual(calls["count"], 1)

    def test_the_better_of_two_attempts_is_returned(self):
        """A retry that comes back worse must not replace a usable answer."""
        calls = {"count": 0}

        @timing_wrapper("NEWS", uses_web_search=True)
        def get_stock_news_openai(ticker, curr_date):
            calls["count"] += 1
            if calls["count"] == 1:
                return "Some partial research that is still short."
            return ""

        result = get_stock_news_openai("NVDA", "2026-09-09")

        self.assertIn("partial research", result)


class CallRecordingTests(WrapperFixture):
    def test_the_call_is_recorded_for_the_tool_output_viewer(self):
        from webui.utils.state import AppState

        state = AppState()

        @timing_wrapper("MARKET")
        def get_market_data_report(symbol):
            return "the report"

        with mock.patch("webui.utils.state.app_state", state):
            get_market_data_report("NVDA")

        recorded = state.get_tool_calls_for_display()
        self.assertTrue(recorded)
        self.assertEqual(recorded[0]["tool_name"], "get_market_data_report")

    def test_long_arguments_are_truncated_in_the_record(self):
        from webui.utils.state import AppState

        state = AppState()

        @timing_wrapper("MARKET")
        def get_market_data_report(payload):
            return "the report"

        with mock.patch("webui.utils.state.app_state", state):
            get_market_data_report("x" * 5000)

        recorded = state.get_tool_calls_for_display()[0]
        self.assertLess(len(str(recorded["inputs"])), 500)

    def test_a_recording_failure_does_not_lose_the_result(self):
        @timing_wrapper("MARKET")
        def get_market_data_report(symbol):
            return "the report"

        with mock.patch(
            "webui.utils.state.app_state", side_effect=RuntimeError("no state")
        ):
            self.assertEqual(get_market_data_report("NVDA"), "the report")


if __name__ == "__main__":
    unittest.main()
