"""Tests for the terminal interface.

`cli/` is a documented entry point (the `tradingagents` console script) and
had no tests at all, so nothing caught a broken import, a renamed report
key, or a display helper that raises on a partial run.
"""

from __future__ import annotations

import unittest
from unittest import mock

from typer.testing import CliRunner


REPORT_SECTIONS = (
    "market_report",
    "sentiment_report",
    "news_report",
    "fundamentals_report",
    "macro_report",
    "investment_plan",
    "trader_investment_plan",
    "final_trade_decision",
)


class MessageBufferTests(unittest.TestCase):
    def setUp(self):
        from cli.main import MessageBuffer

        self.buffer = MessageBuffer()

    def test_messages_and_tool_calls_are_bounded(self):
        """The live display keeps a window, not the whole run."""
        from cli.main import MessageBuffer

        buffer = MessageBuffer(max_length=3)
        for index in range(5):
            buffer.add_message("Reasoning", f"message-{index}")
            buffer.add_tool_call("get_stock_news", {"index": index})

        self.assertEqual(len(buffer.messages), 3)
        self.assertEqual(len(buffer.tool_calls), 3)
        self.assertEqual(buffer.messages[-1][2], "message-4")
        self.assertEqual(buffer.tool_calls[-1][1], "get_stock_news")

    def test_every_agent_starts_pending(self):
        self.assertTrue(self.buffer.agent_status)
        self.assertEqual(set(self.buffer.agent_status.values()), {"pending"})

    def test_updating_an_agent_tracks_it_as_current(self):
        self.buffer.update_agent_status("Market Analyst", "in_progress")

        self.assertEqual(self.buffer.agent_status["Market Analyst"], "in_progress")
        self.assertEqual(self.buffer.current_agent, "Market Analyst")

    def test_unknown_agent_is_ignored(self):
        self.buffer.update_agent_status("Nonexistent Analyst", "in_progress")

        self.assertNotIn("Nonexistent Analyst", self.buffer.agent_status)
        self.assertIsNone(self.buffer.current_agent)

    def test_unknown_report_section_is_ignored(self):
        self.buffer.update_report_section("not_a_report", "content")

        self.assertNotIn("not_a_report", self.buffer.report_sections)
        self.assertIsNone(self.buffer.current_report)

    def test_graph_state_keys_all_map_to_report_sections(self):
        """A renamed state key would silently stop rendering."""
        self.assertEqual(set(self.buffer.report_sections), set(REPORT_SECTIONS))

    def test_current_report_follows_the_most_recent_write(self):
        """Analysts finish out of order, so declaration order is not recency."""
        self.buffer.update_report_section("final_trade_decision", "DECISION: BUY")
        self.buffer.update_report_section("market_report", "MARKET: up")

        self.assertIn("Market Analysis", self.buffer.current_report)
        self.assertIn("MARKET: up", self.buffer.current_report)
        self.assertNotIn("DECISION: BUY", self.buffer.current_report)

    def test_current_report_moves_on_with_each_write(self):
        self.buffer.update_report_section("market_report", "MARKET")
        self.assertIn("Market Analysis", self.buffer.current_report)

        self.buffer.update_report_section("macro_report", "MACRO")
        self.assertIn("Macro Analysis", self.buffer.current_report)

    def test_final_report_is_none_until_a_section_lands(self):
        self.assertIsNone(self.buffer.final_report)

    def test_final_report_accumulates_every_section_in_reading_order(self):
        for section in REPORT_SECTIONS:
            self.buffer.update_report_section(section, f"content of {section}")

        report = self.buffer.final_report
        for heading in (
            "## Analyst Team Reports",
            "### Market Analysis",
            "### Social Sentiment",
            "### News Analysis",
            "### Fundamentals Analysis",
            "### Macro Analysis",
            "## Research Team Decision",
            "## Trading Team Plan",
            "## Portfolio Management Decision",
        ):
            self.assertIn(heading, report)

        positions = [
            report.index(heading)
            for heading in (
                "## Analyst Team Reports",
                "## Research Team Decision",
                "## Trading Team Plan",
                "## Portfolio Management Decision",
            )
        ]
        self.assertEqual(positions, sorted(positions))

    def test_final_report_omits_teams_that_never_reported(self):
        """A run that stops after the analysts still renders."""
        self.buffer.update_report_section("market_report", "MARKET")

        report = self.buffer.final_report
        self.assertIn("## Analyst Team Reports", report)
        self.assertNotIn("## Research Team Decision", report)
        self.assertNotIn("## Portfolio Management Decision", report)


class ResearchTeamStatusTests(unittest.TestCase):
    def test_research_team_advances_together(self):
        from cli import main

        main.message_buffer.update_agent_status("Bull Researcher", "pending")
        main.update_research_team_status("in_progress")

        for agent in (
            "Bull Researcher",
            "Bear Researcher",
            "Research Manager",
            "Trader",
        ):
            self.assertEqual(main.message_buffer.agent_status[agent], "in_progress")


class DisplayTests(unittest.TestCase):
    def test_layout_declares_every_region_the_display_writes(self):
        from cli.main import create_layout, update_display

        layout = create_layout()
        for region in ("header", "main", "footer", "upper", "analysis", "progress", "messages"):
            self.assertIsNotNone(layout[region])

        # Raises if update_display addresses a region the layout lacks.
        update_display(layout)

    def test_display_renders_a_partial_final_state(self):
        """A failed run reaches this with most keys missing."""
        from cli.main import display_complete_report

        with mock.patch("cli.main.console"):
            display_complete_report({"market_report": "MARKET"})

    def test_display_renders_a_complete_final_state(self):
        from cli.main import display_complete_report

        final_state = {section: f"content of {section}" for section in REPORT_SECTIONS}
        final_state["investment_debate_state"] = {
            "bull_history": "bull",
            "bear_history": "bear",
            "judge_decision": "judge",
        }
        final_state["risk_debate_state"] = {
            "risky_history": "risky",
            "safe_history": "safe",
            "neutral_history": "neutral",
            "judge_decision": "risk judge",
        }

        with mock.patch("cli.main.console"):
            display_complete_report(final_state)

    def test_display_tolerates_an_empty_final_state(self):
        from cli.main import display_complete_report

        with mock.patch("cli.main.console"):
            display_complete_report({})


class AnalyzeCommandTests(unittest.TestCase):
    """Typer collapses a single-command app, so `python -m cli.main` and the
    `tradingagents` script take no subcommand. That is what the docs say to
    run, and adding a second command would silently change it."""

    def test_the_app_runs_without_a_subcommand(self):
        from cli.main import app

        with mock.patch("cli.main.run_analysis") as run_analysis:
            result = CliRunner().invoke(app, [])

        self.assertEqual(result.exit_code, 0, result.output)
        run_analysis.assert_called_once_with()

    def test_help_describes_what_the_command_does(self):
        """Collapsing discards Typer(help=...), so the command's own
        docstring is the only description a user sees."""
        from cli.main import app

        result = CliRunner().invoke(app, ["--help"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("TradingAgents analysis", result.output)

    def test_analyze_delegates_to_run_analysis(self):
        from cli.main import analyze

        with mock.patch("cli.main.run_analysis") as run_analysis:
            analyze()

        run_analysis.assert_called_once_with()


class AnalystSelectionTests(unittest.TestCase):
    def test_every_analyst_type_is_offered(self):
        """A new analyst that never reaches the choice list is unreachable."""
        from cli.models import AnalystType
        from cli.utils import select_analysts

        captured = {}

        class Answer:
            def ask(self):
                return [AnalystType.MARKET]

        def checkbox(_message, choices, **kwargs):
            captured["values"] = [choice.value for choice in choices]
            return Answer()

        with mock.patch("questionary.checkbox", checkbox):
            selected = select_analysts()

        self.assertEqual(captured["values"], [item.value for item in AnalystType])
        self.assertEqual(selected, [AnalystType.MARKET])

    def test_research_depth_returns_the_chosen_round_count(self):
        from cli.utils import select_research_depth

        class Answer:
            def ask(self):
                return 3

        with mock.patch("questionary.select", lambda *a, **k: Answer()):
            self.assertEqual(select_research_depth(), 3)


if __name__ == "__main__":
    unittest.main()
