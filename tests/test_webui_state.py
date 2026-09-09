"""Tests for the shared WebUI application state.

`AppState` sits behind every panel: it owns the analysis queue, per-symbol
reports and agent statuses, the scheduling modes, and the stream chunks
arriving from the graph. It is the largest untested surface in the UI, and
a mistake here shows up as a report attached to the wrong symbol or a
status that never advances.
"""

from __future__ import annotations

import unittest

from webui.utils.state import AppState


ANALYST_REPORTS = (
    "market_report",
    "sentiment_report",
    "news_report",
    "fundamentals_report",
    "macro_report",
)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.state = AppState()

    def test_symbols_are_analyzed_in_order(self):
        self.state.add_symbols_to_queue(["NVDA", "AAPL"])

        self.assertEqual(self.state.get_next_symbol(), "NVDA")
        self.assertEqual(self.state.get_next_symbol(), "AAPL")

    def test_an_empty_queue_clears_the_analyzing_symbol(self):
        self.state.add_symbols_to_queue(["NVDA"])
        self.state.get_next_symbol()

        self.assertIsNone(self.state.get_next_symbol())
        self.assertIsNone(self.state.analyzing_symbol)

    def test_the_first_symbol_becomes_the_displayed_one(self):
        self.state.add_symbols_to_queue(["NVDA", "AAPL"])
        self.state.get_next_symbol()

        self.assertEqual(self.state.current_symbol, "NVDA")

    def test_later_symbols_do_not_steal_the_display(self):
        """The user chooses which symbol to look at while others analyze."""
        self.state.add_symbols_to_queue(["NVDA", "AAPL"])
        self.state.get_next_symbol()
        self.state.get_next_symbol()

        self.assertEqual(self.state.current_symbol, "NVDA")
        self.assertEqual(self.state.analyzing_symbol, "AAPL")

    def test_dequeuing_initializes_state_for_a_new_symbol(self):
        self.state.add_symbols_to_queue(["NVDA"])
        self.state.get_next_symbol()

        self.assertIsNotNone(self.state.get_state("NVDA"))

    def test_re_analyzing_a_symbol_starts_a_fresh_session(self):
        self.state.add_symbols_to_queue(["NVDA"])
        self.state.get_next_symbol()
        first_session = self.state.get_state("NVDA")["session_id"]
        self.state.get_state("NVDA")["report_timestamps"]["market_report"] = 123

        self.state.add_symbols_to_queue(["NVDA"])
        self.state.get_next_symbol()
        state = self.state.get_state("NVDA")

        self.assertNotEqual(state["session_id"], first_session)
        self.assertEqual(state["report_timestamps"], {})


class SymbolStateTests(unittest.TestCase):
    def setUp(self):
        self.state = AppState()

    def test_a_new_symbol_starts_every_agent_pending(self):
        self.state.init_symbol_state("NVDA")
        statuses = self.state.get_state("NVDA")["agent_statuses"]

        self.assertTrue(statuses)
        self.assertEqual(set(statuses.values()), {"pending"})

    def test_a_new_symbol_starts_with_no_reports(self):
        self.state.init_symbol_state("NVDA")
        reports = self.state.get_state("NVDA")["current_reports"]

        self.assertTrue(reports)
        self.assertTrue(all(value is None for value in reports.values()))

    def test_reports_and_prompts_cover_the_same_keys(self):
        """A prompt is looked up by its report key."""
        self.state.init_symbol_state("NVDA")
        state = self.state.get_state("NVDA")

        self.assertEqual(set(state["current_reports"]), set(state["agent_prompts"]))

    def test_state_for_an_unknown_symbol_is_none(self):
        self.assertIsNone(self.state.get_state("NOPE"))

    def test_current_and_analyzing_state_resolve_independently(self):
        self.state.add_symbols_to_queue(["NVDA", "AAPL"])
        self.state.get_next_symbol()
        self.state.get_next_symbol()

        self.assertIs(self.state.get_current_state(), self.state.get_state("NVDA"))
        self.assertIs(self.state.get_analyzing_state(), self.state.get_state("AAPL"))


class PruningTests(unittest.TestCase):
    def test_symbol_states_are_capped(self):
        state = AppState()
        for index in range(state.max_symbols + 3):
            state.init_symbol_state(f"S{index}")

        self.assertEqual(len(state.symbol_states), state.max_symbols)

    def test_the_oldest_symbols_go_first(self):
        state = AppState()
        for index in range(state.max_symbols + 2):
            state.init_symbol_state(f"S{index}")

        self.assertNotIn("S0", state.symbol_states)
        self.assertIn(f"S{state.max_symbols + 1}", state.symbol_states)

    def test_the_displayed_and_analyzing_symbols_are_never_pruned(self):
        state = AppState()
        state.init_symbol_state("WATCHED")
        state.init_symbol_state("RUNNING")
        state.current_symbol = "WATCHED"
        state.analyzing_symbol = "RUNNING"

        for index in range(state.max_symbols + 5):
            state.init_symbol_state(f"S{index}")

        self.assertIn("WATCHED", state.symbol_states)
        self.assertIn("RUNNING", state.symbol_states)

    def test_the_screener_pseudo_symbol_is_never_pruned(self):
        state = AppState()
        state.init_symbol_state("SCREENER")
        for index in range(state.max_symbols + 5):
            state.init_symbol_state(f"S{index}")

        self.assertIn("SCREENER", state.symbol_states)

    def test_an_explicit_limit_overrides_the_default(self):
        state = AppState()
        for index in range(6):
            state.init_symbol_state(f"S{index}")

        state.prune_symbol_states(max_limit=2)

        self.assertEqual(len(state.symbol_states), 2)


class AgentStatusTests(unittest.TestCase):
    def setUp(self):
        self.state = AppState()
        self.state.add_symbols_to_queue(["NVDA"])
        self.state.get_next_symbol()
        self.state.needs_ui_update = False

    def test_a_status_change_flags_a_ui_refresh(self):
        self.state.update_agent_status("Market Analyst", "in_progress")

        self.assertEqual(
            self.state.get_state("NVDA")["agent_statuses"]["Market Analyst"],
            "in_progress",
        )
        self.assertTrue(self.state.needs_ui_update)

    def test_rewriting_the_same_status_does_not_flag_a_refresh(self):
        self.state.update_agent_status("Market Analyst", "pending")

        self.assertFalse(self.state.needs_ui_update)

    def test_an_unrecognized_status_falls_back_to_pending(self):
        self.state.update_agent_status("Market Analyst", "exploded")

        self.assertEqual(
            self.state.get_state("NVDA")["agent_statuses"]["Market Analyst"], "pending"
        )

    def test_an_unknown_agent_is_ignored(self):
        self.state.update_agent_status("Nonexistent Analyst", "completed")

        self.assertNotIn(
            "Nonexistent Analyst", self.state.get_state("NVDA")["agent_statuses"]
        )

    def test_an_explicit_symbol_overrides_the_analyzing_one(self):
        self.state.init_symbol_state("AAPL")

        self.state.update_agent_status("Market Analyst", "completed", symbol="AAPL")

        self.assertEqual(
            self.state.get_state("AAPL")["agent_statuses"]["Market Analyst"], "completed"
        )
        self.assertEqual(
            self.state.get_state("NVDA")["agent_statuses"]["Market Analyst"], "pending"
        )


class AgentPromptTests(unittest.TestCase):
    def test_a_prompt_round_trips_for_the_current_symbol(self):
        state = AppState()
        state.add_symbols_to_queue(["NVDA"])
        state.get_next_symbol()

        state.store_agent_prompt("market_report", "You are a market analyst.")

        self.assertEqual(
            state.get_agent_prompt("market_report"), "You are a market analyst."
        )

    def test_an_unstored_prompt_is_none(self):
        state = AppState()
        state.init_symbol_state("NVDA")
        state.current_symbol = "NVDA"

        self.assertIsNone(state.get_agent_prompt("market_report"))

    def test_prompts_do_not_leak_between_symbols(self):
        state = AppState()
        state.init_symbol_state("NVDA")
        state.init_symbol_state("AAPL")

        state.store_agent_prompt("market_report", "nvda prompt", symbol="NVDA")

        self.assertEqual(state.get_agent_prompt("market_report", symbol="NVDA"), "nvda prompt")
        self.assertIsNone(state.get_agent_prompt("market_report", symbol="AAPL"))


class LlmCallTests(unittest.TestCase):
    def test_registering_a_call_counts_it_and_flags_a_refresh(self):
        state = AppState()

        state.register_llm_call(model_name="gpt-5.4-mini", purpose="market")

        self.assertEqual(state.llm_calls_count, 1)
        self.assertTrue(state.needs_ui_update)

    def test_the_payload_keeps_what_the_cost_panel_reads(self):
        state = AppState()

        state.register_llm_call(
            model_name="gpt-5.4-mini",
            purpose="market",
            latency_seconds=1.5,
            usage={"input_tokens": 10, "output_tokens": 20},
        )
        _timestamp, kind, payload = state.llm_calls_log[0]

        self.assertEqual(kind, "LLM_CALL")
        self.assertEqual(payload["model"], "gpt-5.4-mini")
        self.assertEqual(payload["usage"], {"input_tokens": 10, "output_tokens": 20})
        self.assertEqual(payload["status"], "success")

    def test_a_failed_call_is_still_counted(self):
        """Cost and rate-limit debugging needs the failures too."""
        state = AppState()

        state.register_llm_call(status="error", error_message="rate limited")

        self.assertEqual(state.llm_calls_count, 1)
        self.assertEqual(state.llm_calls_log[0][2]["error_message"], "rate limited")

    def test_missing_usage_defaults_to_an_empty_mapping(self):
        state = AppState()

        state.register_llm_call(model_name="gpt-5.4-nano")

        self.assertEqual(state.llm_calls_log[0][2]["usage"], {})


class ResetTests(unittest.TestCase):
    def test_reset_clears_symbols_queue_and_counters(self):
        state = AppState()
        state.add_symbols_to_queue(["NVDA"])
        state.get_next_symbol()
        state.register_llm_call(model_name="gpt-5.4-nano")

        state.reset()

        self.assertEqual(state.symbol_states, {})
        self.assertEqual(state.analysis_queue, [])
        self.assertEqual(state.llm_calls_count, 0)
        self.assertEqual(state.llm_calls_log, [])
        self.assertFalse(state.analysis_running)

    def test_reset_clears_both_symbol_pointers(self):
        """A dangling analyzing_symbol is what update_agent_status resolves
        to by default, and its state no longer exists."""
        state = AppState()
        state.add_symbols_to_queue(["NVDA"])
        state.get_next_symbol()

        state.reset()

        self.assertIsNone(state.current_symbol)
        self.assertIsNone(state.analyzing_symbol)

    def test_reset_leaves_the_screener_status_usable(self):
        state = AppState()
        state.reset()

        self.assertFalse(state.screener_status["is_running"])
        self.assertEqual(state.screener_cooldown, {})


class ToolCallDisplayTests(unittest.TestCase):
    def setUp(self):
        self.state = AppState()

    def test_a_structured_call_passes_through(self):
        call = {
            "timestamp": "10:00:00",
            "tool_name": "get_stock_news",
            "inputs": {"symbol": "NVDA"},
            "agent_type": "News Analyst",
        }
        self.state.tool_calls_log.append(call)

        self.assertEqual(self.state.get_tool_calls_for_display(), [call])

    def test_a_legacy_tuple_is_upgraded(self):
        self.state.tool_calls_log.append(("10:00:00", "get_stock_news", {"symbol": "NVDA"}))

        formatted = self.state.get_tool_calls_for_display()[0]

        self.assertEqual(formatted["tool_name"], "get_stock_news")
        self.assertEqual(formatted["status"], "completed")
        self.assertEqual(formatted["agent_type"], "Unknown Agent")

    def test_an_unrecognized_entry_becomes_an_error_row(self):
        """A malformed log entry must not take the whole panel down."""
        self.state.tool_calls_log.append("just a string")

        formatted = self.state.get_tool_calls_for_display()[0]

        self.assertEqual(formatted["status"], "error")
        self.assertIn("just a string", formatted["output"])

    def test_filtering_by_symbol_is_case_insensitive(self):
        self.state.tool_calls_log.extend(
            [
                {"tool_name": "a", "symbol": "nvda"},
                {"tool_name": "b", "symbol": "AAPL"},
            ]
        )

        filtered = self.state.get_tool_calls_for_display(symbol_filter="NVDA")

        self.assertEqual([call["tool_name"] for call in filtered], ["a"])

    def test_filtering_by_report_type_maps_to_agent_names(self):
        self.state.tool_calls_log.extend(
            [
                {"tool_name": "a", "agent_type": "Market Analyst"},
                {"tool_name": "b", "agent_type": "News Analyst"},
            ]
        )

        filtered = self.state.get_tool_calls_for_display(agent_filter="market_report")

        self.assertEqual([call["tool_name"] for call in filtered], ["a"])

    def test_report_type_aliases_all_resolve(self):
        for report_type, agent in (
            ("sentiment_report", "Social Analyst"),
            ("fundamentals_report", "Fundamentals Analyst"),
            ("macro_report", "Macro Analyst"),
            ("bull_report", "Bull Researcher"),
            ("bear_report", "Bear Researcher"),
            ("research_manager_report", "Research Manager"),
        ):
            self.assertTrue(
                self.state._matches_agent_type(report_type, agent), (report_type, agent)
            )

    def test_an_unrelated_agent_does_not_match(self):
        self.assertFalse(self.state._matches_agent_type("market_report", "News Analyst"))


class SchedulingModeTests(unittest.TestCase):
    def test_loop_mode_starts_and_stops(self):
        state = AppState()

        state.start_loop(["NVDA"], {"depth": 1})
        self.assertTrue(state.loop_enabled)
        self.assertFalse(state.stop_loop)

        state.stop_loop_mode()
        self.assertFalse(state.loop_enabled)
        self.assertTrue(state.stop_loop)
        self.assertFalse(state.analysis_running)

    def test_market_hour_mode_keeps_its_hours(self):
        state = AppState()

        state.start_market_hour_mode(["NVDA"], {"depth": 1}, [10, 15])
        self.assertTrue(state.market_hour_enabled)
        self.assertEqual(state.market_hours, [10, 15])

        state.stop_market_hour_mode()
        self.assertFalse(state.market_hour_enabled)
        self.assertTrue(state.stop_market_hour)

    def test_screener_mode_starts_and_stops(self):
        state = AppState()

        state.start_screener_mode({"depth": 1})
        self.assertTrue(state.screener_enabled)

        state.stop_screener_mode()
        self.assertFalse(state.screener_enabled)
        self.assertTrue(state.stop_screener)

    def test_the_cooldown_map_is_a_copy(self):
        """Callers filter it; mutating the returned map must not persist."""
        state = AppState()
        state.record_analysis_time("NVDA")

        returned = state.get_cooldown_map()
        returned["AAPL"] = 0

        self.assertIn("NVDA", state.screener_cooldown)
        self.assertNotIn("AAPL", state.screener_cooldown)


class CompletionTests(unittest.TestCase):
    def test_no_loop_symbols_is_not_complete(self):
        self.assertFalse(AppState().is_all_symbols_complete())

    def test_completion_requires_every_loop_symbol(self):
        state = AppState()
        state.loop_symbols = ["NVDA", "AAPL"]
        state.init_symbol_state("NVDA")
        state.init_symbol_state("AAPL")
        state.get_state("NVDA")["analysis_complete"] = True

        self.assertFalse(state.is_all_symbols_complete())

        state.get_state("AAPL")["analysis_complete"] = True
        self.assertTrue(state.is_all_symbols_complete())

    def test_a_missing_symbol_state_blocks_completion(self):
        state = AppState()
        state.loop_symbols = ["NVDA", "GONE"]
        state.init_symbol_state("NVDA")
        state.get_state("NVDA")["analysis_complete"] = True

        self.assertFalse(state.is_all_symbols_complete())

    def test_report_count_spans_symbols_and_ignores_blanks(self):
        state = AppState()
        state.init_symbol_state("NVDA")
        state.init_symbol_state("AAPL")
        state.get_state("NVDA")["current_reports"]["market_report"] = "content"
        state.get_state("NVDA")["current_reports"]["news_report"] = "   "
        state.get_state("AAPL")["current_reports"]["macro_report"] = "content"

        state.update_reports_count()

        self.assertEqual(state.generated_reports_count, 2)


class ChunkProcessingTests(unittest.TestCase):
    def setUp(self):
        self.state = AppState()
        self.state.add_symbols_to_queue(["NVDA"])
        self.state.get_next_symbol()

    def _reports(self):
        return self.state.get_state("NVDA")["current_reports"]

    def test_a_report_chunk_is_stored_against_the_analyzed_symbol(self):
        self.state.process_chunk_updates({"market_report": "Momentum is positive."})

        self.assertEqual(self._reports()["market_report"], "Momentum is positive.")

    def test_an_empty_report_is_ignored(self):
        self.state.process_chunk_updates({"market_report": "   "})

        self.assertIsNone(self._reports()["market_report"])

    def test_a_none_report_is_ignored(self):
        self.state.process_chunk_updates({"market_report": None})

        self.assertIsNone(self._reports()["market_report"])

    def test_chunks_without_a_target_symbol_are_dropped_quietly(self):
        state = AppState()

        state.process_chunk_updates({"market_report": "content"})  # must not raise

    def test_a_longer_report_replaces_a_completed_one(self):
        """A completed analyst can still deliver its final, fuller report."""
        self.state.process_chunk_updates({"market_report": "short"})
        self.state.update_agent_status("Market Analyst", "completed")

        self.state.process_chunk_updates({"market_report": "a much longer final report body"})

        self.assertEqual(self._reports()["market_report"], "a much longer final report body")

    def test_a_shorter_report_does_not_overwrite_a_completed_one(self):
        """Streaming fragments arrive after completion; they must not win."""
        full = "a much longer final report body"
        self.state.process_chunk_updates({"market_report": full})
        self.state.update_agent_status("Market Analyst", "completed")

        self.state.process_chunk_updates({"market_report": "frag"})

        self.assertEqual(self._reports()["market_report"], full)

    def test_every_analyst_report_key_is_accepted(self):
        for index, report_type in enumerate(ANALYST_REPORTS):
            self.state.process_chunk_updates({report_type: f"content {index}"})

        for index, report_type in enumerate(ANALYST_REPORTS):
            self.assertEqual(self._reports()[report_type], f"content {index}", report_type)


if __name__ == "__main__":
    unittest.main()


class LoopResetTests(unittest.TestCase):
    """Loop mode reuses symbol states across iterations."""

    def setUp(self):
        self.state = AppState()
        self.state.init_symbol_state("NVDA")
        self.state.current_symbol = "NVDA"

    def test_reset_describes_the_same_reports_as_a_fresh_symbol(self):
        """A dropped key means the panel reading it behaves differently on
        the second loop iteration than on the first."""
        fresh = self.state.get_state("NVDA")
        expected_reports = set(fresh["current_reports"])
        expected_prompts = set(fresh["agent_prompts"])

        self.state.reset_for_loop()
        after = self.state.get_state("NVDA")

        self.assertEqual(set(after["current_reports"]), expected_reports)
        self.assertEqual(set(after["agent_prompts"]), expected_prompts)

    def test_the_previous_iteration_reports_are_cleared(self):
        state = self.state.get_state("NVDA")
        state["current_reports"]["market_report"] = "stale"
        state["analysis_complete"] = True

        self.state.reset_for_loop()
        after = self.state.get_state("NVDA")

        self.assertIsNone(after["current_reports"]["market_report"])
        self.assertFalse(after["analysis_complete"])

    def test_the_symbol_survives_so_pagination_keeps_working(self):
        self.state.reset_for_loop()

        self.assertIn("NVDA", self.state.symbol_states)

    def test_each_iteration_gets_a_new_session(self):
        first = self.state.get_state("NVDA")["session_id"]

        self.state.reset_for_loop()

        self.assertNotEqual(self.state.get_state("NVDA")["session_id"], first)

    def test_the_queue_and_counters_are_cleared(self):
        self.state.add_symbols_to_queue(["AAPL"])
        self.state.register_llm_call(model_name="gpt-5.4-nano")

        self.state.reset_for_loop()

        self.assertEqual(self.state.analysis_queue, [])
        self.assertEqual(self.state.llm_calls_count, 0)

    def test_every_agent_returns_to_pending(self):
        self.state.update_agent_status("Market Analyst", "completed", symbol="NVDA")

        self.state.reset_for_loop()

        statuses = self.state.get_state("NVDA")["agent_statuses"]
        self.assertEqual(set(statuses.values()), {"pending"})


class DebateChunkTests(unittest.TestCase):
    def setUp(self):
        self.state = AppState()
        self.state.add_symbols_to_queue(["NVDA"])
        self.state.get_next_symbol()

    def _reports(self):
        return self.state.get_state("NVDA")["current_reports"]

    def _statuses(self):
        return self.state.get_state("NVDA")["agent_statuses"]

    def test_the_bull_and_bear_arguments_are_stored(self):
        self.state.process_chunk_updates(
            {
                "investment_debate_state": {
                    "bull_history": "the bull case",
                    "bear_history": "the bear case",
                }
            }
        )

        self.assertEqual(self._reports()["bull_report"], "the bull case")
        self.assertEqual(self._reports()["bear_report"], "the bear case")

    def test_the_latest_message_wins_over_the_full_history(self):
        """The panel shows the newest turn, not the whole transcript."""
        self.state.process_chunk_updates(
            {
                "investment_debate_state": {
                    "bull_history": "turn one\nturn two",
                    "bull_messages": ["turn one", "turn two"],
                }
            }
        )

        self.assertEqual(self._reports()["bull_report"], "turn two")

    def test_the_manager_decision_completes_the_research_team(self):
        self.state.process_chunk_updates(
            {"investment_debate_state": {"judge_decision": "go long"}}
        )

        self.assertEqual(self._statuses()["Research Manager"], "completed")
        self.assertEqual(self._statuses()["Bull Researcher"], "completed")
        self.assertEqual(self._statuses()["Trader"], "in_progress")
        self.assertEqual(self._reports()["investment_plan"], "go long")

    def test_the_trader_plan_hands_over_to_the_risk_team(self):
        self.state.process_chunk_updates({"trader_investment_plan": "the plan"})

        self.assertEqual(self._reports()["trader_investment_plan"], "the plan")
        self.assertEqual(self._statuses()["Trader"], "completed")
        self.assertEqual(self._statuses()["Risky Analyst"], "in_progress")

    def test_each_risk_perspective_is_stored(self):
        self.state.process_chunk_updates(
            {
                "risk_debate_state": {
                    "current_risky_response": "press on",
                    "current_safe_response": "trim",
                    "current_neutral_response": "hold",
                }
            }
        )

        self.assertIn("press on", self._reports()["risky_report"])
        self.assertIn("trim", self._reports()["safe_report"])
        self.assertIn("hold", self._reports()["neutral_report"])

    def test_the_risk_verdict_completes_the_portfolio_manager(self):
        self.state.process_chunk_updates(
            {"risk_debate_state": {"judge_decision": "BUY 10 shares"}}
        )

        self.assertEqual(self._statuses()["Portfolio Manager"], "completed")
        self.assertEqual(self._reports()["final_trade_decision"], "BUY 10 shares")

    def test_the_verdict_backfills_perspectives_from_their_history(self):
        """A run that only streamed the transcript still shows all three."""
        self.state.process_chunk_updates(
            {
                "risk_debate_state": {
                    "judge_decision": "HOLD",
                    "risky_history": "Risky Analyst: press on",
                    "safe_history": "Safe Analyst: trim",
                    "neutral_history": "Neutral Analyst: hold",
                }
            }
        )

        self.assertEqual(self._reports()["risky_report"], "press on")
        self.assertEqual(self._reports()["safe_report"], "trim")
        self.assertEqual(self._reports()["neutral_report"], "hold")

    def test_the_typed_intent_is_kept_for_execution(self):
        intent = {"action": "BUY", "symbol": "NVDA"}

        self.state.process_chunk_updates({"final_trade_intent": intent})

        self.assertEqual(self.state.get_state("NVDA")["final_trade_intent"], intent)

    def test_an_empty_debate_state_changes_nothing(self):
        self.state.process_chunk_updates({"investment_debate_state": {}})

        self.assertIsNone(self._reports()["bull_report"])
