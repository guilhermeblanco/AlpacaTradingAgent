"""Tests for the graph's routing decisions.

These predicates decide whether an analyst loops back to its tools, who
speaks next in each debate, and when a debate is over. Getting one wrong
either strands the graph in a loop or cuts an argument short, and neither
shows up as an error — only as a worse decision.
"""

from __future__ import annotations

import unittest

from langchain_core.messages import AIMessage

from tradingagents.graph.conditional_logic import ConditionalLogic


def _message(tool_calls=()):
    return AIMessage(content="", tool_calls=list(tool_calls))


def _tool_call():
    return {"name": "get_stock_news", "args": {}, "id": "call_1", "type": "tool_call"}


class AnalystRoutingTests(unittest.TestCase):
    ANALYSTS = (
        ("should_continue_market", "tools_market", "Msg Clear Market"),
        ("should_continue_social", "tools_social", "Msg Clear Social"),
        ("should_continue_news", "tools_news", "Msg Clear News"),
        (
            "should_continue_fundamentals",
            "tools_fundamentals",
            "Msg Clear Fundamentals",
        ),
        ("should_continue_macro", "tools_macro", "Msg Clear Macro"),
    )

    def setUp(self):
        self.logic = ConditionalLogic()

    def test_a_pending_tool_call_routes_to_that_analysts_tools(self):
        state = {"messages": [_message([_tool_call()])]}

        for method, tools_node, _done in self.ANALYSTS:
            self.assertEqual(getattr(self.logic, method)(state), tools_node)

    def test_a_plain_answer_ends_the_analyst_loop(self):
        state = {"messages": [_message()]}

        for method, _tools_node, done in self.ANALYSTS:
            self.assertEqual(getattr(self.logic, method)(state), done)

    def test_only_the_last_message_decides(self):
        """Earlier tool calls in the transcript are already answered."""
        state = {"messages": [_message([_tool_call()]), _message()]}

        self.assertEqual(self.logic.should_continue_market(state), "Msg Clear Market")


def _debate(count=0, current_response=""):
    return {
        "investment_debate_state": {
            "count": count,
            "current_response": current_response,
        }
    }


class ResearchDebateRoutingTests(unittest.TestCase):
    def test_the_bull_speaks_first(self):
        logic = ConditionalLogic(max_debate_rounds=1)

        self.assertEqual(logic.should_continue_debate(_debate()), "Bull Researcher")

    def test_the_bear_answers_the_bull(self):
        logic = ConditionalLogic(max_debate_rounds=1)

        self.assertEqual(
            logic.should_continue_debate(_debate(1, "Bull Analyst: growth")),
            "Bear Researcher",
        )

    def test_the_bull_answers_the_bear(self):
        logic = ConditionalLogic(max_debate_rounds=1)

        self.assertEqual(
            logic.should_continue_debate(_debate(1, "Bear Analyst: margins")),
            "Bull Researcher",
        )

    def test_the_debate_ends_after_two_turns_per_round(self):
        logic = ConditionalLogic(max_debate_rounds=1)

        self.assertEqual(
            logic.should_continue_debate(_debate(2, "Bear Analyst: margins")),
            "Research Manager",
        )

    def test_more_rounds_keep_the_debate_open_longer(self):
        logic = ConditionalLogic(max_debate_rounds=3)

        self.assertEqual(
            logic.should_continue_debate(_debate(2, "Bear Analyst: margins")),
            "Bull Researcher",
        )
        self.assertEqual(
            logic.should_continue_debate(_debate(6, "Bear Analyst: margins")),
            "Research Manager",
        )


def _risk(count=0, latest_speaker=None):
    inner = {"count": count}
    if latest_speaker is not None:
        inner["latest_speaker"] = latest_speaker
    return {"risk_debate_state": inner}


class RiskDebateRoutingTests(unittest.TestCase):
    def test_the_three_perspectives_take_turns_in_order(self):
        logic = ConditionalLogic(max_risk_discuss_rounds=2)

        self.assertEqual(logic.should_continue_risk_analysis(_risk(1, "Risky")), "Safe Analyst")
        self.assertEqual(
            logic.should_continue_risk_analysis(_risk(2, "Safe")), "Neutral Analyst"
        )
        self.assertEqual(
            logic.should_continue_risk_analysis(_risk(3, "Neutral")), "Risky Analyst"
        )

    def test_a_missing_speaker_starts_the_rotation_at_risky(self):
        """The first pass through has nobody on record yet."""
        logic = ConditionalLogic(max_risk_discuss_rounds=1)
        state = _risk(0)

        self.assertEqual(logic.should_continue_risk_analysis(state), "Safe Analyst")
        self.assertEqual(state["risk_debate_state"]["latest_speaker"], "Risky")

    def test_the_risk_debate_ends_after_three_turns_per_round(self):
        logic = ConditionalLogic(max_risk_discuss_rounds=1)

        self.assertEqual(
            logic.should_continue_risk_analysis(_risk(3, "Neutral")), "Risk Judge"
        )

    def test_more_rounds_keep_the_risk_debate_open_longer(self):
        logic = ConditionalLogic(max_risk_discuss_rounds=2)

        self.assertEqual(
            logic.should_continue_risk_analysis(_risk(3, "Neutral")), "Risky Analyst"
        )
        self.assertEqual(
            logic.should_continue_risk_analysis(_risk(6, "Neutral")), "Risk Judge"
        )


if __name__ == "__main__":
    unittest.main()
