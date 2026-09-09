"""Tests for LangGraph wiring.

`GraphSetup` decides which analysts exist, whether they run in parallel,
and how their results are merged back into one state. A wiring mistake here
does not raise — it quietly drops an analyst's report or loses a debate
round, so the run completes with less evidence than it should have.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from langchain_core.messages import AIMessage, HumanMessage

from tradingagents.graph.conditional_logic import ConditionalLogic
from tradingagents.graph.setup import GraphSetup


ALL_ANALYSTS = ["market", "social", "news", "fundamentals", "macro"]

REPORT_KEY = {
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
    "macro": "macro_report",
}


class FakeLLM:
    def bind_tools(self, _tools):
        return self

    def invoke(self, _input, **_kwargs):
        return AIMessage(content="unused")


def _toolkit():
    return SimpleNamespace(
        config={"online_tools": False, "max_tool_iterations_per_agent": 1},
        has_research_market_data=lambda _t: False,
        has_openai_web_search=lambda *a, **k: False,
        has_finnhub=lambda *a, **k: False,
        has_coindesk=lambda *a, **k: False,
        has_simfin_data=lambda *a, **k: False,
        has_fred=lambda *a, **k: False,
    )


def _setup(config=None):
    merged = {
        "parallel_analysts": True,
        "analyst_call_delay": 0,
        "analyst_start_delay": 0,
        "risk_analyst_start_delay": 0,
        "tool_result_delay": 0,
        "max_debate_rounds": 1,
        "max_risk_discuss_rounds": 1,
        "parallel_risk_first_round": True,
    }
    merged.update(config or {})
    memory = SimpleNamespace(get_memories=lambda *a, **k: [])
    return GraphSetup(
        quick_thinking_llm=FakeLLM(),
        deep_thinking_llm=FakeLLM(),
        toolkit=_toolkit(),
        tool_nodes={name: (lambda state: {}) for name in ALL_ANALYSTS},
        bull_memory=memory,
        bear_memory=memory,
        trader_memory=memory,
        invest_judge_memory=memory,
        risk_manager_memory=memory,
        conditional_logic=ConditionalLogic(
            max_debate_rounds=merged["max_debate_rounds"],
            max_risk_discuss_rounds=merged["max_risk_discuss_rounds"],
        ),
        config=merged,
    )


def _state(**overrides):
    state = {
        "company_of_interest": "NVDA",
        "trade_date": "2026-09-09",
        "messages": [HumanMessage(content="go")],
    }
    state.update(overrides)
    return state


class SelectionTests(unittest.TestCase):
    def test_no_analysts_is_rejected(self):
        """An empty graph would report a decision with no evidence at all."""
        with self.assertRaises(ValueError) as raised:
            _setup().setup_graph([])

        self.assertIn("no analysts selected", str(raised.exception))

    def test_the_full_roster_compiles(self):
        graph = _setup().setup_graph(ALL_ANALYSTS)

        self.assertIsNotNone(graph)

    def test_a_single_analyst_compiles(self):
        for analyst in ALL_ANALYSTS:
            self.assertIsNotNone(_setup().setup_graph([analyst]), analyst)

    def test_a_subset_compiles(self):
        self.assertIsNotNone(_setup().setup_graph(["market", "macro"]))

    def test_sequential_mode_compiles(self):
        graph = _setup({"parallel_analysts": False}).setup_graph(ALL_ANALYSTS)

        self.assertIsNotNone(graph)

    def test_sequential_mode_compiles_for_one_analyst(self):
        graph = _setup({"parallel_analysts": False}).setup_graph(["news"])

        self.assertIsNotNone(graph)


class RunLoggingWrapperTests(unittest.TestCase):
    def test_the_wrapped_node_returns_what_the_node_returned(self):
        setup = _setup()
        wrapped = setup._wrap_node_with_run_logging(
            "Market Analyst", lambda state: {"market_report": "the read"}
        )

        with mock.patch("tradingagents.graph.setup.get_run_audit_logger"):
            self.assertEqual(wrapped(_state()), {"market_report": "the read"})

    def test_a_logger_failure_does_not_break_the_node(self):
        """Audit logging is observability, not part of the analysis."""
        setup = _setup()
        wrapped = setup._wrap_node_with_run_logging(
            "Market Analyst", lambda state: {"market_report": "the read"}
        )

        with mock.patch(
            "tradingagents.graph.setup.get_run_audit_logger",
            side_effect=RuntimeError("logger down"),
        ):
            try:
                result = wrapped(_state())
            except RuntimeError:
                self.fail("a logging failure must not propagate")

        self.assertEqual(result, {"market_report": "the read"})

    def test_a_node_exception_still_propagates(self):
        setup = _setup()

        def explode(_state):
            raise RuntimeError("analyst failed")

        wrapped = setup._wrap_node_with_run_logging("Market Analyst", explode)

        with mock.patch("tradingagents.graph.setup.get_run_audit_logger"):
            with self.assertRaises(RuntimeError):
                wrapped(_state())


class ParallelAnalystCoordinatorTests(unittest.TestCase):
    def _coordinator(self, analysts, nodes):
        setup = _setup()
        return setup._create_parallel_analysts_coordinator(
            analysts,
            nodes,
            {name: (lambda state: {}) for name in analysts},
            {name: (lambda state: {}) for name in analysts},
        )

    def test_every_analyst_report_reaches_the_merged_state(self):
        analysts = ["market", "news", "macro"]
        nodes = {
            name: (lambda key: lambda state: {key: f"{key} content"})(REPORT_KEY[name])
            for name in analysts
        }

        result = self._coordinator(analysts, nodes)(_state())

        for name in analysts:
            self.assertEqual(result[REPORT_KEY[name]], f"{REPORT_KEY[name]} content")

    def test_one_failing_analyst_does_not_lose_the_others(self):
        """A provider outage on one source must not void the whole run."""

        def explode(_state):
            raise RuntimeError("provider down")

        nodes = {
            "market": lambda state: {"market_report": "market content"},
            "news": explode,
        }

        result = self._coordinator(["market", "news"], nodes)(_state())

        self.assertEqual(result["market_report"], "market content")

    def test_analysts_do_not_see_each_others_edits(self):
        """Each runs on its own copy, so a mutation cannot leak sideways."""
        seen = {}

        def mutating(state):
            state["company_of_interest"] = "MUTATED"
            return {"market_report": "market content"}

        def observer(state):
            seen["symbol"] = state["company_of_interest"]
            return {"news_report": "news content"}

        self._coordinator(["market", "news"], {"market": mutating, "news": observer})(
            _state()
        )

        self.assertEqual(seen["symbol"], "NVDA")

    def test_messages_from_the_analysts_are_collected(self):
        nodes = {
            "market": lambda state: {
                "market_report": "content",
                "messages": [AIMessage(content="market said")],
            }
        }

        result = self._coordinator(["market"], nodes)(_state())

        self.assertIn("messages", result)


class ParallelRiskCoordinatorTests(unittest.TestCase):
    def _coordinator(self, nodes):
        return _setup()._create_parallel_risk_round_one_coordinator(nodes)

    @staticmethod
    def _risk_node(key, text):
        def node(state):
            debate = dict(state.get("risk_debate_state") or {})
            debate[key] = text
            return {"risk_debate_state": debate}

        return node

    def test_all_three_perspectives_land_in_one_debate_state(self):
        nodes = {
            "Risky": self._risk_node("current_risky_response", "risky view"),
            "Safe": self._risk_node("current_safe_response", "safe view"),
            "Neutral": self._risk_node("current_neutral_response", "neutral view"),
        }

        result = self._coordinator(nodes)(
            _state(risk_debate_state={"history": "", "count": 0})
        )
        debate = result["risk_debate_state"]

        self.assertEqual(debate["current_risky_response"], "risky view")
        self.assertEqual(debate["current_safe_response"], "safe view")
        self.assertEqual(debate["current_neutral_response"], "neutral view")

    def test_one_failing_perspective_does_not_lose_the_others(self):
        def explode(_state):
            raise RuntimeError("model refused")

        nodes = {
            "Risky": self._risk_node("current_risky_response", "risky view"),
            "Safe": explode,
            "Neutral": self._risk_node("current_neutral_response", "neutral view"),
        }

        result = self._coordinator(nodes)(
            _state(risk_debate_state={"history": "", "count": 0})
        )
        debate = result["risk_debate_state"]

        self.assertEqual(debate["current_risky_response"], "risky view")
        self.assertEqual(debate["current_neutral_response"], "neutral view")

    def test_the_round_counter_advances(self):
        nodes = {
            "Risky": self._risk_node("current_risky_response", "risky view"),
            "Safe": self._risk_node("current_safe_response", "safe view"),
            "Neutral": self._risk_node("current_neutral_response", "neutral view"),
        }

        result = self._coordinator(nodes)(
            _state(risk_debate_state={"history": "", "count": 0})
        )

        self.assertGreaterEqual(result["risk_debate_state"]["count"], 1)


class LoggingIsolationTests(unittest.TestCase):
    """Every logging call in the wrapper must be unable to fail a node."""

    def test_a_logger_that_raises_on_every_call_is_tolerated(self):
        setup = _setup()

        class ExplodingLogger:
            def log_event(self, **_kwargs):
                raise RuntimeError("disk full")

            def log_agent_output(self, **_kwargs):
                raise RuntimeError("disk full")

        wrapped = setup._wrap_node_with_run_logging(
            "Market Analyst",
            lambda state: {
                "market_report": "the read",
                "report_context": {"stats": {"macro_report": 1}},
                "investment_debate_state": {"current_response": "bull says"},
                "risk_debate_state": {
                    "latest_speaker": "Risky",
                    "current_risky_response": "risky says",
                },
            },
        )

        with mock.patch(
            "tradingagents.graph.setup.get_run_audit_logger",
            lambda: ExplodingLogger(),
        ):
            result = wrapped(_state())

        self.assertEqual(result["market_report"], "the read")

    def test_a_non_dict_result_is_passed_straight_through(self):
        setup = _setup()
        wrapped = setup._wrap_node_with_run_logging(
            "Market Analyst", lambda state: "not a dict"
        )

        with mock.patch("tradingagents.graph.setup.get_run_audit_logger"):
            self.assertEqual(wrapped(_state()), "not a dict")


if __name__ == "__main__":
    unittest.main()
