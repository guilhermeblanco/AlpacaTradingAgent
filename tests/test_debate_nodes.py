"""Tests for the research, trading, and risk graph nodes.

Between the analysts and the final decision sit two debates and a judge for
each. Every node here appends to a shared transcript, and the risk manager
turns the last of it into the typed intent execution acts on — so what
these accumulate is the evidence the trade is made from.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable

from tradingagents.agents.managers.research_manager import create_research_manager
from tradingagents.agents.managers.risk_manager import create_risk_manager
from tradingagents.agents.researchers.bear_researcher import create_bear_researcher
from tradingagents.agents.researchers.bull_researcher import create_bull_researcher
from tradingagents.agents.risk_mgmt.aggresive_debator import create_risky_debator
from tradingagents.agents.risk_mgmt.conservative_debator import create_safe_debator
from tradingagents.agents.risk_mgmt.neutral_debator import create_neutral_debator
from tradingagents.agents.trader.trader import create_trader


class FakeLLM(Runnable):
    """Answers with scripted text and records what it was asked."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.prompts = []

    def bind_tools(self, _tools):
        return self

    def with_structured_output(self, *_args, **_kwargs):
        return self

    def invoke(self, input, config=None, **kwargs):  # noqa: A002 - Runnable API
        """Nodes retry (structured output, then free text, then a fallback
        prompt), so the last scripted answer keeps being given."""
        self.prompts.append(input)
        if not self._responses:
            return AIMessage(content="a considered argument")
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


class Memory:
    def __init__(self, memories=()):
        self._memories = list(memories)
        self.queries = []

    def get_memories(self, situation, n_matches=2):
        self.queries.append(situation)
        return self._memories


def _reports():
    return {
        "market_report": "Momentum is positive.",
        "sentiment_report": "Sentiment is constructive.",
        "news_report": "No adverse headlines.",
        "fundamentals_report": "Margins expanding.",
        "macro_report": "Rates steady.",
    }


def _state(**overrides):
    state = {
        "company_of_interest": "NVDA",
        "trade_date": "2026-09-09",
        "messages": [],
        "current_position": "NEUTRAL",
        "investment_debate_state": {
            "history": "",
            "bull_history": "",
            "bear_history": "",
            "current_response": "",
            "count": 0,
        },
        "risk_debate_state": {
            "history": "",
            "risky_history": "",
            "safe_history": "",
            "neutral_history": "",
            "current_risky_response": "",
            "current_safe_response": "",
            "current_neutral_response": "",
            "latest_speaker": "",
            "count": 0,
        },
        "investment_plan": "Accumulate on strength.",
        "trader_investment_plan": "Buy 1% of equity.",
        **_reports(),
    }
    state.update(overrides)
    return state


class ResearcherTests(unittest.TestCase):
    def test_the_bull_appends_to_its_own_side_of_the_transcript(self):
        llm = FakeLLM(AIMessage(content="Growth is accelerating."))

        result = create_bull_researcher(llm, Memory())(_state())
        debate = result["investment_debate_state"]

        self.assertIn("Growth is accelerating.", debate["bull_history"])
        self.assertIn("Growth is accelerating.", debate["history"])
        self.assertEqual(debate["bear_history"], "")

    def test_the_bear_appends_to_its_own_side(self):
        llm = FakeLLM(AIMessage(content="Margins are compressing."))

        debate = create_bear_researcher(llm, Memory())(_state())[
            "investment_debate_state"
        ]

        self.assertIn("Margins are compressing.", debate["bear_history"])
        self.assertEqual(debate["bull_history"], "")

    def test_the_round_counter_advances(self):
        llm = FakeLLM(AIMessage(content="argument"))

        debate = create_bull_researcher(llm, Memory())(_state())[
            "investment_debate_state"
        ]

        self.assertEqual(debate["count"], 1)

    def test_the_reply_becomes_the_response_the_other_side_answers(self):
        llm = FakeLLM(AIMessage(content="Growth is accelerating."))

        debate = create_bull_researcher(llm, Memory())(_state())[
            "investment_debate_state"
        ]

        self.assertIn("Growth is accelerating.", debate["current_response"])

    def test_an_existing_transcript_is_preserved(self):
        llm = FakeLLM(AIMessage(content="second turn"))
        state = _state()
        state["investment_debate_state"]["bull_history"] = "first turn"
        state["investment_debate_state"]["history"] = "first turn"

        debate = create_bull_researcher(llm, Memory())(state)[
            "investment_debate_state"
        ]

        self.assertIn("first turn", debate["bull_history"])
        self.assertIn("second turn", debate["bull_history"])

    def test_past_lessons_are_retrieved_for_the_situation(self):
        memory = Memory([{"recommendation": "size down after a gap"}])

        create_bull_researcher(FakeLLM(), memory)(_state())

        self.assertTrue(memory.queries)

    def test_lessons_reach_the_prompt(self):
        memory = Memory([{"recommendation": "size down after a gap"}])
        llm = FakeLLM()

        create_bull_researcher(llm, memory)(_state())

        self.assertIn("size down after a gap", str(llm.prompts[0]))


class ResearchManagerTests(unittest.TestCase):
    def test_the_judge_writes_the_investment_plan(self):
        llm = FakeLLM(AIMessage(content="Accumulate in thirds."))

        result = create_research_manager(llm, Memory())(_state())

        self.assertIn("Accumulate in thirds.", result["investment_plan"])

    def test_the_verdict_is_recorded_on_the_debate(self):
        llm = FakeLLM(AIMessage(content="Accumulate in thirds."))

        result = create_research_manager(llm, Memory())(_state())

        self.assertIn(
            "Accumulate in thirds.",
            result["investment_debate_state"]["judge_decision"],
        )

    def test_the_judge_sees_the_whole_debate_transcript(self):
        llm = FakeLLM(AIMessage(content="verdict"))
        state = _state()
        state["investment_debate_state"]["history"] = (
            "Bull: the bull case\nBear: the bear case"
        )

        create_research_manager(llm, Memory())(state)

        prompt = str(llm.prompts[0])
        self.assertIn("the bull case", prompt)
        self.assertIn("the bear case", prompt)

    def test_the_analyst_evidence_is_scored_into_a_claim_matrix(self):
        """The judge adjudicates on scored claims, not on raw report text."""
        llm = FakeLLM(AIMessage(content="verdict"))

        create_research_manager(llm, Memory())(_state())

        prompt = str(llm.prompts[0])
        self.assertIn("Claim Matrix", prompt)
        self.assertIn("Margins expanding.", prompt)


class TraderTests(unittest.TestCase):
    def _run(self, llm, *, position="NEUTRAL", state=None):
        provider = mock.MagicMock()
        with mock.patch(
            "tradingagents.agents.trader.trader.build_broker_prompt_context",
            lambda *a, **k: (position, "no positions", "equity $100,000"),
        ):
            node = create_trader(llm, Memory(), config={}, snapshot_provider=provider)
            return node(state or _state())

    def test_the_trader_writes_a_plan(self):
        result = self._run(FakeLLM(AIMessage(content="Buy 1% of equity.")))

        self.assertIn("trader_investment_plan", result)
        self.assertTrue(result["trader_investment_plan"])

    def test_the_plan_carries_a_final_proposal(self):
        """Downstream extraction keys off this line."""
        result = self._run(FakeLLM(AIMessage(content="Buy 1% of equity.")))

        self.assertIn(
            "FINAL TRANSACTION PROPOSAL", result["trader_investment_plan"].upper()
        )

    def test_the_current_broker_position_reaches_the_prompt(self):
        llm = FakeLLM(AIMessage(content="Trim to half."))

        self._run(llm, position="LONG")

        self.assertIn("LONG", str(llm.prompts[0]))

    def test_a_substantial_research_plan_reaches_the_prompt(self):
        llm = FakeLLM(AIMessage(content="plan"))
        state = _state(investment_plan="Accumulate on weakness only. " * 12)

        self._run(llm, state=state)

        self.assertIn("Accumulate on weakness only.", str(llm.prompts[0]))

    def test_a_too_thin_research_plan_is_replaced_by_a_full_analysis_request(self):
        """A one-line plan is not something to trade off, so the trader is
        asked to build the setup itself rather than passing the stub along."""
        llm = FakeLLM(AIMessage(content="plan"))
        state = _state(investment_plan="Buy it.")

        self._run(llm, state=state)

        prompt = str(llm.prompts[0])
        self.assertNotIn("Buy it.", prompt)
        self.assertIn("REQUIRED SWING TRADING PLAN", prompt)


class RiskDebatorTests(unittest.TestCase):
    FACTORIES = (
        (create_risky_debator, "risky_history", "current_risky_response", "Risky"),
        (create_safe_debator, "safe_history", "current_safe_response", "Safe"),
        (create_neutral_debator, "neutral_history", "current_neutral_response", "Neutral"),
    )

    def test_each_perspective_appends_to_its_own_side(self):
        for factory, history_key, response_key, _speaker in self.FACTORIES:
            llm = FakeLLM(AIMessage(content=f"{history_key} argument"))

            debate = factory(llm, config={})(_state())["risk_debate_state"]

            self.assertIn(f"{history_key} argument", debate[history_key])
            self.assertIn(f"{history_key} argument", debate[response_key])

    def test_each_perspective_names_itself_as_the_speaker(self):
        for factory, _history_key, _response_key, speaker in self.FACTORIES:
            debate = factory(FakeLLM(), config={})(_state())["risk_debate_state"]

            self.assertEqual(debate["latest_speaker"], speaker)

    def test_the_round_counter_advances(self):
        debate = create_risky_debator(FakeLLM(), config={})(_state())[
            "risk_debate_state"
        ]

        self.assertEqual(debate["count"], 1)

    def test_a_perspective_sees_the_trader_plan(self):
        llm = FakeLLM(AIMessage(content="argument"))
        state = _state(trader_investment_plan="Buy 2% on the open.")

        create_risky_debator(llm, config={})(state)

        self.assertIn("Buy 2% on the open.", str(llm.prompts[0]))

    def test_a_perspective_sees_the_others_replies(self):
        llm = FakeLLM(AIMessage(content="argument"))
        state = _state()
        state["risk_debate_state"]["current_safe_response"] = "trim exposure"

        create_risky_debator(llm, config={})(state)

        self.assertIn("trim exposure", str(llm.prompts[0]))


class RiskManagerTests(unittest.TestCase):
    def _run(self, llm, *, state=None, config=None):
        with mock.patch(
            "tradingagents.agents.managers.risk_manager.build_broker_prompt_context",
            lambda *a, **k: ("NEUTRAL", "no positions", "equity $100,000"),
            create=True,
        ):
            node = create_risk_manager(llm, Memory(), config=config or {})
            return node(state or _state())

    def test_the_final_decision_is_produced(self):
        result = self._run(
            FakeLLM(AIMessage(content="FINAL TRANSACTION PROPOSAL: **BUY**"))
        )

        self.assertIn("final_trade_decision", result)
        self.assertTrue(result["final_trade_decision"])

    def test_a_typed_intent_accompanies_the_decision(self):
        """Execution acts on the intent, not on the prose."""
        result = self._run(
            FakeLLM(AIMessage(content="FINAL TRANSACTION PROPOSAL: **BUY**"))
        )

        self.assertIn("final_trade_intent", result)

    def test_the_recommendation_is_extracted(self):
        result = self._run(
            FakeLLM(AIMessage(content="FINAL TRANSACTION PROPOSAL: **BUY**"))
        )

        self.assertEqual(result["recommended_action"], "BUY")

    def test_the_verdict_is_recorded_on_the_risk_debate(self):
        result = self._run(
            FakeLLM(AIMessage(content="FINAL TRANSACTION PROPOSAL: **HOLD**"))
        )

        self.assertIn("judge_decision", result["risk_debate_state"])

    def test_the_trading_mode_is_reported(self):
        result = self._run(
            FakeLLM(AIMessage(content="FINAL TRANSACTION PROPOSAL: **HOLD**"))
        )

        self.assertIn(result["trading_mode"], ("investment", "trading"))


if __name__ == "__main__":
    unittest.main()
