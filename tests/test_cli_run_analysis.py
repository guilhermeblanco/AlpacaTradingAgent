"""Tests for the CLI run pipeline.

`run_analysis` wires the interactive prompts to the graph and the live
display. The parts that decide what a run actually is — the configuration,
which analyst starts, what carries over between runs — are extracted so
they can be checked without driving a terminal session.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from cli.main import (
    MessageBuffer,
    build_run_config,
    first_analyst_label,
    provider_needs_backend_url,
    reset_message_buffer,
    run_analysis,
)
from cli.models import AnalystType
from tradingagents.default_config import DEFAULT_CONFIG


def _selections(**overrides):
    selections = {
        "ticker": "NVDA",
        "analysis_date": "2026-09-09",
        "analysts": [AnalystType.MARKET, AnalystType.NEWS],
        "research_depth": 3,
        "llm_provider": "openai",
        "backend_url": "",
        "checkpoint_enabled": False,
        "output_language": "English",
        "google_thinking_level": "",
        "anthropic_effort": "",
        "shallow_thinker": "gpt-5.4-nano",
        "deep_thinker": "gpt-5.4-mini",
    }
    selections.update(overrides)
    return selections


class BackendUrlProviderTests(unittest.TestCase):
    def test_the_prompt_follows_the_model_registry(self):
        """A provider added to the registry must not need a second edit here."""
        from tradingagents.openai_model_registry import (
            get_llm_provider_options,
            get_provider_ui_metadata,
        )

        providers = [option["value"] for option in get_llm_provider_options()]
        self.assertTrue(providers)
        for provider in providers:
            self.assertEqual(
                provider_needs_backend_url(provider),
                bool(get_provider_ui_metadata(provider).get("backend_visible", False)),
                provider,
            )

    def test_hosted_openai_needs_no_endpoint(self):
        self.assertFalse(provider_needs_backend_url("openai"))

    def test_local_and_proxied_providers_do(self):
        for provider in ("local_openai", "ollama", "openrouter", "azure"):
            self.assertTrue(provider_needs_backend_url(provider), provider)

    def test_an_unknown_provider_is_not_offered_the_prompt(self):
        self.assertFalse(provider_needs_backend_url("made-up"))


class RunConfigTests(unittest.TestCase):
    def test_research_depth_drives_both_debate_budgets(self):
        config = build_run_config(_selections(research_depth=5))

        self.assertEqual(config["max_debate_rounds"], 5)
        self.assertEqual(config["max_risk_discuss_rounds"], 5)

    def test_the_chosen_models_and_provider_are_carried_over(self):
        config = build_run_config(
            _selections(
                llm_provider="anthropic",
                shallow_thinker="claude-haiku-4-5-20251001",
                deep_thinker="claude-opus-5",
            )
        )

        self.assertEqual(config["llm_provider"], "anthropic")
        self.assertEqual(config["quick_think_llm"], "claude-haiku-4-5-20251001")
        self.assertEqual(config["deep_think_llm"], "claude-opus-5")

    def test_a_blank_backend_url_becomes_none(self):
        """An empty string would override the provider default endpoint."""
        self.assertIsNone(build_run_config(_selections(backend_url=""))["backend_url"])

    def test_a_supplied_backend_url_is_kept(self):
        config = build_run_config(_selections(backend_url="http://localhost:1234/v1"))

        self.assertEqual(config["backend_url"], "http://localhost:1234/v1")

    def test_provider_knobs_are_omitted_unless_chosen(self):
        config = build_run_config(_selections())

        self.assertEqual(
            config["google_thinking_level"], DEFAULT_CONFIG["google_thinking_level"]
        )
        self.assertEqual(config["anthropic_effort"], DEFAULT_CONFIG["anthropic_effort"])

    def test_a_chosen_gemini_thinking_level_is_applied(self):
        config = build_run_config(_selections(google_thinking_level="high"))

        self.assertEqual(config["google_thinking_level"], "high")

    def test_a_chosen_claude_effort_is_applied(self):
        config = build_run_config(_selections(anthropic_effort="medium"))

        self.assertEqual(config["anthropic_effort"], "medium")

    def test_the_cli_always_runs_in_investment_mode(self):
        """The CLI has no shorting switch, so it must not inherit one."""
        self.assertEqual(build_run_config(_selections())["trading_mode"], "investment")

    def test_checkpoint_and_language_are_carried_over(self):
        config = build_run_config(
            _selections(checkpoint_enabled=True, output_language="Portuguese")
        )

        self.assertTrue(config["checkpoint_enabled"])
        self.assertEqual(config["output_language"], "Portuguese")

    def test_the_shared_default_config_is_not_mutated(self):
        before = dict(DEFAULT_CONFIG)

        build_run_config(_selections(research_depth=5))

        self.assertEqual(DEFAULT_CONFIG, before)

    def test_unrelated_defaults_survive(self):
        config = build_run_config(_selections())

        self.assertEqual(config["results_dir"], DEFAULT_CONFIG["results_dir"])


class FirstAnalystTests(unittest.TestCase):
    def test_the_first_selected_analyst_starts_the_run(self):
        self.assertEqual(
            first_analyst_label([AnalystType.NEWS, AnalystType.MARKET]), "News Analyst"
        )

    def test_every_analyst_type_maps_to_a_known_agent(self):
        """A label the buffer does not know would leave the run showing
        every agent pending."""
        buffer = MessageBuffer()

        for analyst in AnalystType:
            label = first_analyst_label([analyst])
            self.assertIn(label, buffer.agent_status, analyst)

    def test_no_analysts_yields_no_label(self):
        self.assertIsNone(first_analyst_label([]))


class BufferResetTests(unittest.TestCase):
    def test_a_previous_run_leaves_nothing_behind(self):
        buffer = MessageBuffer()
        buffer.update_agent_status("Market Analyst", "completed")
        buffer.update_report_section("market_report", "stale content")

        reset_message_buffer(buffer)

        self.assertEqual(set(buffer.agent_status.values()), {"pending"})
        self.assertTrue(all(value is None for value in buffer.report_sections.values()))
        self.assertIsNone(buffer.current_report)
        self.assertIsNone(buffer.final_report)

    def test_the_next_write_after_a_reset_is_the_one_displayed(self):
        """last_updated_section must clear too, or the panel opens on a
        section from the previous run."""
        buffer = MessageBuffer()
        buffer.update_report_section("final_trade_decision", "old decision")

        reset_message_buffer(buffer)
        buffer.update_report_section("market_report", "new market read")

        self.assertIn("Market Analysis", buffer.current_report)
        self.assertNotIn("old decision", buffer.current_report)


class RunAnalysisTests(unittest.TestCase):
    """The wiring itself: selections in, configured graph and streamed run out."""

    def setUp(self):
        self.graph = mock.MagicMock()
        self.compiled = mock.MagicMock()
        self.graph._graph_for_run.return_value = (self.compiled, None)
        self.graph._graph_args_for_run.return_value = {}
        self.graph.propagator.create_initial_state.return_value = {"messages": []}
        self.graph.process_signal.return_value = "BUY"
        self.constructed = {}

    def _build_graph(self, analysts, config=None, debug=False):
        self.constructed["analysts"] = analysts
        self.constructed["config"] = config
        self.constructed["debug"] = debug
        return self.graph

    def _run(self, selections, chunks, on_display=None):
        self.compiled.stream.return_value = iter(chunks)
        patches = [
            mock.patch("cli.main.get_user_selections", lambda: selections),
            mock.patch("cli.main.TradingAgentsGraph", self._build_graph),
            mock.patch("cli.main.get_run_audit_logger", mock.MagicMock()),
            mock.patch("cli.main.Live", mock.MagicMock()),
            mock.patch("cli.main.update_display", on_display or mock.MagicMock()),
            mock.patch("cli.main.display_complete_report", mock.MagicMock()),
            mock.patch("cli.main.console", mock.MagicMock()),
            mock.patch("cli.main.trade_intent_action", lambda _intent: "BUY"),
        ]
        for patch in patches:
            patch.start()
        try:
            run_analysis()
        finally:
            for patch in patches:
                patch.stop()

    @staticmethod
    def _final_chunk(message=None):
        messages = [message] if message is not None else []
        return {
            "messages": messages,
            "final_trade_decision": "FINAL TRANSACTION PROPOSAL: BUY",
            "final_trade_intent": {"action": "BUY"},
            "trading_mode": "investment",
        }

    @staticmethod
    def _message(content, tool_calls=()):
        message = mock.MagicMock()
        message.content = content
        message.tool_calls = list(tool_calls)
        return message

    def test_the_graph_is_built_from_the_selections(self):
        self._run(
            _selections(research_depth=5, llm_provider="anthropic"),
            [self._final_chunk()],
        )

        self.assertEqual(self.constructed["analysts"], ["market", "news"])
        self.assertEqual(self.constructed["config"]["max_debate_rounds"], 5)
        self.assertEqual(self.constructed["config"]["llm_provider"], "anthropic")
        self.assertTrue(self.constructed["debug"])

    def test_the_run_targets_the_selected_symbol_and_date(self):
        self._run(
            _selections(ticker="AAPL", analysis_date="2026-01-02"),
            [self._final_chunk()],
        )

        self.graph.propagator.create_initial_state.assert_called_once_with(
            "AAPL", "2026-01-02"
        )
        self.graph._graph_for_run.assert_called_once_with("AAPL", "2026-01-02")

    def test_the_decision_is_recorded_against_the_run(self):
        self._run(_selections(ticker="AAPL"), [self._final_chunk()])

        self.graph.memory_log.store_decision.assert_called_once()
        recorded = self.graph.memory_log.store_decision.call_args.kwargs
        self.assertEqual(recorded["ticker"], "AAPL")
        self.assertEqual(recorded["trading_mode"], "investment")

    def test_streamed_messages_reach_the_display_buffer(self):
        from cli.main import message_buffer

        self._run(
            _selections(),
            [self._final_chunk(self._message("Reasoning about momentum"))],
        )

        self.assertTrue(
            any(
                "Reasoning about momentum" in str(entry)
                for entry in message_buffer.messages
            )
        )

    def test_streamed_tool_calls_are_recorded(self):
        from cli.main import message_buffer

        message = self._message(
            "calling a tool",
            [{"name": "get_stock_news", "args": {"symbol": "NVDA"}}],
        )

        self._run(_selections(), [self._final_chunk(message)])

        self.assertTrue(
            any("get_stock_news" in str(entry) for entry in message_buffer.tool_calls)
        )

    def test_an_empty_stream_reports_what_went_wrong(self):
        """trace[-1] on an empty stream is an IndexError that says nothing
        about the run that produced it."""
        with self.assertRaises(RuntimeError) as raised:
            self._run(_selections(ticker="AAPL"), [])

        self.assertIn("produced no output", str(raised.exception))
        self.assertIn("AAPL", str(raised.exception))

    def test_a_failed_run_is_logged_before_the_error_propagates(self):
        logger = mock.MagicMock()
        self.compiled.stream.side_effect = RuntimeError("provider exploded")
        patches = [
            mock.patch("cli.main.get_user_selections", lambda: _selections()),
            mock.patch("cli.main.TradingAgentsGraph", self._build_graph),
            mock.patch("cli.main.get_run_audit_logger", lambda: logger),
            mock.patch("cli.main.Live", mock.MagicMock()),
            mock.patch("cli.main.update_display", mock.MagicMock()),
            mock.patch("cli.main.console", mock.MagicMock()),
        ]
        for patch in patches:
            patch.start()
        try:
            with self.assertRaises(RuntimeError):
                run_analysis()
        finally:
            for patch in patches:
                patch.stop()

        logger.finish_run.assert_called_once()
        self.assertEqual(logger.finish_run.call_args.kwargs["status"], "failed")



class StreamHandlingTests(RunAnalysisTests):
    """Each streamed key advances the right agent and fills the right report."""

    def _run_with(self, chunk, analysts=None):
        """Snapshot the buffer mid-stream.

        run_analysis marks every agent completed once the run ends, so the
        interesting transitions are only visible while it is streaming.
        """
        from cli.main import message_buffer

        selections = _selections(
            analysts=analysts
            or [
                AnalystType.MARKET,
                AnalystType.SOCIAL,
                AnalystType.NEWS,
                AnalystType.FUNDAMENTALS,
                AnalystType.MACRO,
            ]
        )
        snapshots = []
        streaming = {"active": True}

        def capture(*_args, **_kwargs):
            if streaming["active"]:
                snapshots.append(
                    (
                        dict(message_buffer.agent_status),
                        dict(message_buffer.report_sections),
                    )
                )

        merged = {**self._final_chunk(), **chunk}

        def stream():
            yield merged
            # The loop asks for the next chunk after it has processed this
            # one, so everything from here on is the end-of-run sweep.
            streaming["active"] = False

        self._run(selections, stream(), on_display=capture)
        statuses, reports = snapshots[-1]
        return SimpleNamespace(agent_status=statuses, report_sections=reports)

    def test_each_analyst_report_completes_its_analyst(self):
        for report_type, agent in (
            ("market_report", "Market Analyst"),
            ("sentiment_report", "Social Analyst"),
            ("news_report", "News Analyst"),
            ("fundamentals_report", "Fundamentals Analyst"),
            ("macro_report", "Macro Analyst"),
        ):
            buffer = self._run_with({report_type: f"{report_type} body"})

            self.assertEqual(buffer.report_sections[report_type], f"{report_type} body")
            self.assertEqual(buffer.agent_status[agent], "completed", agent)

    def test_finishing_one_analyst_starts_the_next_selected_one(self):
        buffer = self._run_with({"market_report": "body"})

        self.assertEqual(buffer.agent_status["Social Analyst"], "in_progress")

    def test_an_unselected_next_analyst_is_left_alone(self):
        buffer = self._run_with(
            {"market_report": "body"}, analysts=[AnalystType.MARKET]
        )

        self.assertEqual(buffer.agent_status["Social Analyst"], "pending")

    def test_the_macro_report_hands_over_to_the_research_team(self):
        buffer = self._run_with({"macro_report": "body"})

        self.assertEqual(buffer.agent_status["Bull Researcher"], "in_progress")
        self.assertEqual(buffer.agent_status["Trader"], "in_progress")

    def test_an_empty_report_does_not_complete_its_analyst(self):
        """The first analyst is already in_progress; a blank report must not
        advance it to completed."""
        buffer = self._run_with({"market_report": ""})

        self.assertEqual(buffer.agent_status["Market Analyst"], "in_progress")
        self.assertIsNone(buffer.report_sections["market_report"])

    def test_the_investment_debate_fills_the_research_reports(self):
        buffer = self._run_with(
            {
                "investment_debate_state": {
                    "bull_history": "bull opening\nbull rebuttal",
                    "bear_history": "bear opening\nbear rebuttal",
                    "judge_decision": "the manager decides",
                }
            }
        )

        self.assertIn("bull rebuttal", buffer.report_sections["investment_plan"])
        self.assertEqual(buffer.agent_status["Research Manager"], "completed")

    def test_the_risk_debate_completes_the_portfolio_manager(self):
        buffer = self._run_with(
            {
                "risk_debate_state": {
                    "current_risky_response": "risky view",
                    "current_safe_response": "safe view",
                    "current_neutral_response": "neutral view",
                    "judge_decision": "final call",
                }
            }
        )

        self.assertEqual(buffer.agent_status["Portfolio Manager"], "completed")
        self.assertIn("final call", buffer.report_sections["final_trade_decision"])

    def test_the_trader_plan_advances_the_risk_team(self):
        buffer = self._run_with({"trader_investment_plan": "the trade plan"})

        self.assertEqual(
            buffer.report_sections["trader_investment_plan"], "the trade plan"
        )
        self.assertEqual(buffer.agent_status["Risky Analyst"], "in_progress")


class UserSelectionTests(unittest.TestCase):
    """get_user_selections drives the prompts and assembles their answers."""

    def _run(self, **overrides):
        answers = {
            "get_ticker": "NVDA",
            "select_analysts": [AnalystType.MARKET],
            "select_research_depth": 3,
            "select_llm_provider": "openai",
            "get_backend_url": "http://localhost:1234/v1",
            "select_shallow_thinking_agent": "gpt-5.4-nano",
            "select_deep_thinking_agent": "gpt-5.4-mini",
            "ask_gemini_thinking_config": "high",
            "ask_anthropic_effort": "medium",
            "select_checkpoint_enabled": True,
            "get_output_language": "English",
        }
        answers.update(overrides)
        patches = [mock.patch("cli.main.console", mock.MagicMock())]
        patches += [
            mock.patch(f"cli.main.{name}", (lambda v: lambda *a, **k: v)(value))
            for name, value in answers.items()
        ]
        for patch in patches:
            patch.start()
        try:
            from cli.main import get_user_selections

            return get_user_selections()
        finally:
            for patch in patches:
                patch.stop()

    def test_the_answers_are_collected_into_one_mapping(self):
        selections = self._run()

        self.assertEqual(selections["ticker"], "NVDA")
        self.assertEqual(selections["research_depth"], 3)
        self.assertEqual(selections["shallow_thinker"], "gpt-5.4-nano")
        self.assertEqual(selections["deep_thinker"], "gpt-5.4-mini")
        self.assertTrue(selections["checkpoint_enabled"])

    def test_the_analysis_date_is_today(self):
        import datetime

        selections = self._run()

        self.assertEqual(
            selections["analysis_date"],
            datetime.datetime.now().strftime("%Y-%m-%d"),
        )

    def test_hosted_openai_is_not_asked_for_an_endpoint(self):
        selections = self._run(select_llm_provider="openai")

        self.assertEqual(selections["backend_url"], "")

    def test_a_local_provider_is_asked_for_an_endpoint(self):
        selections = self._run(select_llm_provider="ollama")

        self.assertEqual(selections["backend_url"], "http://localhost:1234/v1")

    def test_gemini_thinking_is_only_asked_of_google(self):
        self.assertEqual(
            self._run(select_llm_provider="google")["google_thinking_level"], "high"
        )
        self.assertEqual(
            self._run(select_llm_provider="openai")["google_thinking_level"], ""
        )

    def test_claude_effort_is_only_asked_of_anthropic(self):
        self.assertEqual(
            self._run(select_llm_provider="anthropic")["anthropic_effort"], "medium"
        )
        self.assertEqual(
            self._run(select_llm_provider="openai")["anthropic_effort"], ""
        )

    def test_the_result_feeds_straight_into_the_run_config(self):
        """The two halves are only ever used together."""
        config = build_run_config(self._run())

        self.assertEqual(config["quick_think_llm"], "gpt-5.4-nano")
        self.assertEqual(config["max_debate_rounds"], 3)


if __name__ == "__main__":
    unittest.main()
