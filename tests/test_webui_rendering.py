"""Tests for the WebUI rendering helpers.

These turn accumulated run state into the HTML the debate tabs, progress
table, and prompt viewer show. They are pure given state, and they are what
a user actually reads, so a parsing miss here silently blanks a panel.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from webui.components.ui import (
    render_agent_status_table,
    render_progress_stats,
    render_researcher_debate,
    render_risk_debate,
    update_chart_period,
    update_ui,
)
from webui.utils import reddit_fix
from webui.utils.prompt_capture import (
    PromptCapture,
    capture_agent_prompt,
    get_agent_prompt,
)
from webui.utils.state import AppState


class StateFixture(unittest.TestCase):
    """Each test gets its own AppState patched over the shared singleton."""

    def setUp(self):
        self.state = AppState()
        patches = [
            mock.patch("webui.components.ui.app_state", self.state),
            mock.patch("webui.utils.prompt_capture.app_state", self.state),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _prepare(self, symbol="NVDA"):
        self.state.init_symbol_state(symbol)
        self.state.current_symbol = symbol
        self.state.analyzing_symbol = symbol
        return self.state.get_state(symbol)


class ResearcherDebateTests(StateFixture):
    def test_no_symbol_renders_nothing(self):
        self.assertEqual(render_researcher_debate(None), "<p></p>")

    def test_an_unknown_symbol_explains_itself(self):
        rendered = render_researcher_debate("UNKNOWN")

        self.assertIn("No active analysis", rendered)

    def test_the_emoji_transcript_is_split_by_speaker(self):
        state = self._prepare()
        state["investment_debate_state"] = {
            "history": "🐂 Bull Researcher\nGrowth is accelerating.\n"
            "🐻 Bear Researcher\nMargins are compressing."
        }

        rendered = render_researcher_debate("NVDA")

        self.assertIn("Growth is accelerating", rendered)
        self.assertIn("Margins are compressing", rendered)

    def test_the_legacy_transcript_format_still_parses(self):
        state = self._prepare()
        state["investment_debate_state"] = {
            "history": "Bull Analyst: Growth is strong.\nBear Analyst: Valuation is rich."
        }

        rendered = render_researcher_debate("NVDA")

        self.assertIn("Growth is strong", rendered)
        self.assertIn("Valuation is rich", rendered)

    def test_an_empty_debate_still_renders(self):
        state = self._prepare()
        state["investment_debate_state"] = {"history": ""}

        self.assertIsInstance(render_researcher_debate("NVDA"), str)

    def test_a_missing_debate_state_still_renders(self):
        self._prepare()

        self.assertIsInstance(render_researcher_debate("NVDA"), str)

    def test_windows_line_endings_are_handled(self):
        state = self._prepare()
        state["investment_debate_state"] = {
            "history": "🐂 Bull Researcher\r\nGrowth.\r\n🐻 Bear Researcher\r\nRisk."
        }

        rendered = render_researcher_debate("NVDA")

        self.assertIn("Growth", rendered)
        self.assertIn("Risk", rendered)


class RiskDebateTests(StateFixture):
    def test_no_symbol_renders_nothing(self):
        self.assertEqual(render_risk_debate(None), "<p></p>")

    def test_an_unknown_symbol_explains_itself(self):
        self.assertIn("No active analysis", render_risk_debate("UNKNOWN"))

    def test_the_three_perspectives_are_rendered(self):
        state = self._prepare()
        state["risk_debate_state"] = {
            "history": "Risky Analyst: Press the advantage.\n"
            "Safe Analyst: Trim exposure.\n"
            "Neutral Analyst: Hold and reassess."
        }

        rendered = render_risk_debate("NVDA")

        self.assertIn("Press the advantage", rendered)
        self.assertIn("Trim exposure", rendered)
        self.assertIn("Hold and reassess", rendered)

    def test_a_missing_debate_state_still_renders(self):
        self._prepare()

        self.assertIsInstance(render_risk_debate("NVDA"), str)


class StatusTableTests(StateFixture):
    def test_no_run_says_so(self):
        self.assertIn("No analysis running", render_agent_status_table())

    def test_every_agent_appears_with_its_status(self):
        self._prepare()
        self.state.update_agent_status("Market Analyst", "completed")
        self.state.update_agent_status("News Analyst", "in_progress")

        rendered = render_agent_status_table()

        self.assertIn("Market Analyst", rendered)
        self.assertIn("COMPLETED", rendered)
        self.assertIn("IN_PROGRESS", rendered)

    def test_each_state_gets_a_distinct_icon(self):
        self._prepare()
        self.state.update_agent_status("Market Analyst", "completed")
        self.state.update_agent_status("News Analyst", "in_progress")

        rendered = render_agent_status_table()

        self.assertIn("✅", rendered)
        self.assertIn("🔄", rendered)
        self.assertIn("⏸️", rendered)


class ProgressStatsTests(StateFixture):
    def test_the_counters_are_reported(self):
        self.state.tool_calls_count = 7
        self.state.llm_calls_count = 3
        self.state.generated_reports_count = 2

        rendered = render_progress_stats()

        self.assertIn("7", rendered)
        self.assertIn("3", rendered)
        self.assertIn("2", rendered)


class UpdateUiTests(StateFixture):
    def test_every_panel_key_is_present(self):
        self._prepare()

        with mock.patch("webui.components.ui.create_welcome_chart", lambda: "chart"):
            payload = update_ui()

        for key in (
            "status_table",
            "progress_stats",
            "market_analysis_report",
            "researcher_debate",
            "final_trade_decision",
            "decision_summary_card",
            "stock_chart",
        ):
            self.assertIn(key, payload, key)

    def test_an_incomplete_run_says_so(self):
        self._prepare()

        with mock.patch("webui.components.ui.create_welcome_chart", lambda: "chart"):
            payload = update_ui()

        self.assertIn("not complete", payload["decision_summary_card"])

    def test_a_finished_run_summarizes_its_decision(self):
        self._prepare()
        self.state.ticker_symbol = "NVDA"
        self.state.analysis_complete = True
        self.state.analysis_results = {"decision": "BUY", "date": "2026-09-09"}

        with mock.patch("webui.components.ui.create_welcome_chart", lambda: "chart"):
            payload = update_ui()

        self.assertIn("BUY", payload["decision_summary_card"])
        self.assertIn("NVDA", payload["decision_summary_card"])


class ChartPeriodTests(StateFixture):
    def test_no_symbol_falls_back_to_the_welcome_chart(self):
        with mock.patch("webui.components.ui.create_welcome_chart", lambda: "welcome"):
            self.assertEqual(update_chart_period("1y"), "welcome")

    def test_a_symbol_gets_a_chart_for_the_period(self):
        self.state.ticker_symbol = "NVDA"

        with mock.patch(
            "webui.components.ui.create_chart", lambda *a, **k: "the chart"
        ):
            self.assertEqual(update_chart_period("6mo"), "the chart")

    def test_a_chart_failure_falls_back_rather_than_raising(self):
        self.state.ticker_symbol = "NVDA"

        def explode(*_a, **_k):
            raise RuntimeError("provider down")

        with mock.patch("webui.components.ui.create_chart", explode), mock.patch(
            "webui.components.ui.create_welcome_chart", lambda: "welcome"
        ):
            self.assertEqual(update_chart_period("1y"), "welcome")


class PromptCaptureTests(StateFixture):
    def test_a_prompt_round_trips_for_a_symbol(self):
        self._prepare()

        with mock.patch("webui.utils.prompt_capture.get_run_audit_logger"):
            capture_agent_prompt("market_report", "You are a market analyst.", "NVDA")

        self.assertEqual(
            get_agent_prompt("market_report", "NVDA"), "You are a market analyst."
        )

    def test_an_uncaptured_prompt_explains_why_it_is_empty(self):
        """The viewer shows this instead of a blank modal."""
        self._prepare()

        message = get_agent_prompt("news_report", "NVDA")

        self.assertIn("not yet captured", message)

    def test_a_string_prompt_is_returned_as_is(self):
        self.assertEqual(
            PromptCapture.extract_system_message_from_prompt("You are an analyst."),
            "You are an analyst.",
        )

    def test_a_chat_template_yields_its_system_message(self):
        from langchain_core.prompts import ChatPromptTemplate

        template = ChatPromptTemplate.from_messages(
            [("system", "You are a market analyst."), ("human", "{query}")]
        )

        extracted = PromptCapture.extract_system_message_from_prompt(template)

        self.assertIn("You are", extracted)

    def test_an_unusable_prompt_object_does_not_raise(self):
        self.assertIsInstance(
            PromptCapture.extract_system_message_from_prompt(object()), str
        )


class RedditDataDirectoryTests(unittest.TestCase):
    def test_a_missing_directory_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("tradingagents.dataflows.config.DATA_DIR", tmp):
                self.assertFalse(reddit_fix.check_reddit_data_directory())

    def test_a_directory_missing_its_subfolders_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(Path(tmp) / "reddit_data")
            with mock.patch("tradingagents.dataflows.config.DATA_DIR", tmp):
                self.assertFalse(reddit_fix.check_reddit_data_directory())

    def test_a_complete_directory_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("company_news", "global_news"):
                folder = Path(tmp) / "reddit_data" / name
                folder.mkdir(parents=True)
                (folder / "data.jsonl").write_text("{}\n", encoding="utf-8")
            with mock.patch("tradingagents.dataflows.config.DATA_DIR", tmp):
                self.assertTrue(reddit_fix.check_reddit_data_directory())

    def test_empty_subfolders_still_pass_with_a_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("company_news", "global_news"):
                (Path(tmp) / "reddit_data" / name).mkdir(parents=True)
            with mock.patch("tradingagents.dataflows.config.DATA_DIR", tmp):
                self.assertTrue(reddit_fix.check_reddit_data_directory())

    def test_mock_data_is_written_where_the_loader_looks(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("tradingagents.dataflows.config.DATA_DIR", tmp):
                reddit_fix.create_mock_reddit_data()

            for name in ("company_news", "global_news"):
                folder = Path(tmp) / "reddit_data" / name
                self.assertTrue(folder.is_dir(), name)
                files = list(folder.glob("*.jsonl"))
                self.assertTrue(files, name)
                first = files[0].read_text(encoding="utf-8").splitlines()[0]
                self.assertIsInstance(json.loads(first), dict)

    def test_the_diagnosis_reports_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("tradingagents.dataflows.config.DATA_DIR", tmp):
                reddit_fix.diagnose_reddit_issues()


if __name__ == "__main__":
    unittest.main()


class PromptCaptureInternalsTests(StateFixture):
    def test_the_capture_records_against_the_analyzed_symbol(self):
        self._prepare("NVDA")
        self.state.analyzing_symbol = "NVDA"

        with mock.patch("webui.utils.prompt_capture.get_run_audit_logger"):
            capture_agent_prompt("news_report", "You are a news analyst.")

        self.assertEqual(
            get_agent_prompt("news_report", "NVDA"), "You are a news analyst."
        )

    def test_a_logging_failure_does_not_lose_the_prompt(self):
        self._prepare("NVDA")

        with mock.patch(
            "webui.utils.prompt_capture.get_run_audit_logger",
            side_effect=RuntimeError("disk full"),
        ):
            capture_agent_prompt("market_report", "the prompt", "NVDA")

        self.assertEqual(get_agent_prompt("market_report", "NVDA"), "the prompt")

    def test_capturing_for_an_unknown_symbol_does_not_raise(self):
        with mock.patch("webui.utils.prompt_capture.get_run_audit_logger"):
            capture_agent_prompt("market_report", "the prompt", "UNKNOWN")

    def test_a_dict_prompt_is_reduced_to_text(self):
        extracted = PromptCapture.extract_system_message_from_prompt(
            {"system": "You are an analyst."}
        )

        self.assertIsInstance(extracted, str)

    def test_a_none_prompt_yields_text(self):
        self.assertIsInstance(
            PromptCapture.extract_system_message_from_prompt(None), str
        )


class UpdateUiReportTests(StateFixture):
    def test_the_reports_reach_their_panels(self):
        self._prepare()
        self.state.current_reports = {
            "market_report": "market body",
            "sentiment_report": "sentiment body",
            "news_report": "news body",
            "fundamentals_report": "fundamentals body",
            "research_manager_report": "manager body",
            "trader_investment_plan": "trader body",
            "risky_report": "risky body",
            "safe_report": "safe body",
            "neutral_report": "neutral body",
            "portfolio_decision": "portfolio body",
            "final_trade_decision": "final body",
        }

        with mock.patch("webui.components.ui.create_welcome_chart", lambda: "chart"):
            payload = update_ui()

        self.assertEqual(payload["market_analysis_report"], "market body")
        self.assertEqual(payload["trader_investment_plan"], "trader body")
        self.assertEqual(payload["final_trade_decision"], "final body")

    def test_a_recorded_chart_is_used_instead_of_the_welcome_chart(self):
        self._prepare()
        self.state.chart_data = "the real chart"

        with mock.patch("webui.components.ui.create_welcome_chart", lambda: "welcome"):
            payload = update_ui()

        self.assertEqual(payload["stock_chart"], "the real chart")
