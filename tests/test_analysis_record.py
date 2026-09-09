"""Tests for the structured claim matrix and the analysis stage record.

The scored evidence a decision rests on used to exist only as text inside a
prompt, and the debates only in graph state that vanished with the process.
Both are now data recorded against the decision id execution uses, so one
query reconstructs what a trade was made of — not just what it did.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from tradingagents.agents.utils.report_context import (
    _render_decision_claim_matrix,
    build_decision_claim_matrix,
    build_report_context_index,
    get_agent_context_bundle,
)
from tradingagents.workbench import (
    ANALYSIS_STAGES_EVENT,
    build_analysis_record,
    record_analysis_stages,
)
from tradingagents.workbench import analysis_record as analysis_record_module


def _state(**overrides):
    state = {
        "company_of_interest": "NVDA",
        "trade_date": "2026-09-09",
        "current_position": "NEUTRAL",
        "market_report": "## Overview\nMomentum is positive, RSI 62 and rising.",
        "sentiment_report": "## Overview\nSentiment is constructive across forums.",
        "news_report": "## Overview\nNo adverse headlines in the past week.",
        "fundamentals_report": "## Overview\nMargins expanded 300bps year on year.",
        "macro_report": "## Overview\nRates steady, curve normal.",
    }
    state.update(overrides)
    return state


class ClaimMatrixStructureTests(unittest.TestCase):
    def setUp(self):
        self.context = build_report_context_index(_state())
        self.matrix = build_decision_claim_matrix(self.context)

    def test_every_analyst_report_appears_as_a_source(self):
        labels = [source["label"] for source in self.matrix["sources"]]

        for expected in ("Market", "News", "Fundamentals", "Macro"):
            self.assertIn(expected, labels)

    def test_a_source_carries_its_dominant_direction(self):
        for source in self.matrix["sources"]:
            if source["claims"] or source["fallback_points"]:
                self.assertIn(
                    source["dominant_direction"], ("Bullish", "Bearish", "Mixed")
                )

    def test_a_claim_carries_the_scores_it_was_adjudicated_on(self):
        claims = [
            claim for source in self.matrix["sources"] for claim in source["claims"]
        ]
        self.assertTrue(claims)

        for field in ("claim_id", "direction", "confidence", "freshness",
                      "numeric_support", "contradiction"):
            self.assertIn(field, claims[0], field)

    def test_the_scoreboard_is_carried_through(self):
        self.assertIn("net_direction", self.matrix["scoreboard"])

    def test_the_totals_match_the_context_statistics(self):
        stats = self.context["stats"]

        self.assertEqual(self.matrix["totals"]["claims"], stats["total_claims"])
        self.assertEqual(self.matrix["totals"]["bullish"], stats["bullish_claims"])

    def test_a_run_with_no_reports_still_produces_a_matrix(self):
        matrix = build_decision_claim_matrix(
            build_report_context_index({"company_of_interest": "NVDA"})
        )

        self.assertEqual(matrix["totals"]["claims"], 0)
        self.assertIsInstance(matrix["sources"], list)


class ClaimMatrixRenderingTests(unittest.TestCase):
    """The prompt text is rendered from the structure, so the two cannot
    drift apart."""

    def test_the_rendered_matrix_names_every_source_the_structure_has(self):
        context = build_report_context_index(_state())
        rendered = _render_decision_claim_matrix(context)

        for source in build_decision_claim_matrix(context)["sources"]:
            self.assertIn(source["label"], rendered)

    def test_the_rendered_matrix_carries_the_claim_ids(self):
        context = build_report_context_index(_state())
        rendered = _render_decision_claim_matrix(context)

        matrix = build_decision_claim_matrix(context)
        claim_ids = [
            claim["claim_id"] for source in matrix["sources"] for claim in source["claims"]
        ]
        self.assertTrue(claim_ids)
        for claim_id in claim_ids:
            self.assertIn(claim_id, rendered)

    def test_a_source_with_nothing_usable_says_so(self):
        context = build_report_context_index(_state(market_report="short"))
        rendered = _render_decision_claim_matrix(context)

        self.assertIn("Decision Claim Matrix", rendered)

    def test_the_bundle_exposes_both_the_text_and_the_structure(self):
        bundle = get_agent_context_bundle(
            _state(), agent_role="manager", objective="decide"
        )

        self.assertIsInstance(bundle["decision_claim_matrix"], str)
        self.assertIn("sources", bundle["decision_claim_matrix_data"])


def _final_state(**overrides):
    state = _state(
        investment_debate_state={
            "count": 2,
            "bull_history": "Bull: growth is accelerating.",
            "bear_history": "Bear: margins are peaking.",
            "history": "Bull: growth\nBear: margins",
            "judge_decision": "Accumulate in thirds.",
        },
        risk_debate_state={
            "count": 3,
            "risky_history": "Risky: size up.",
            "safe_history": "Safe: size down.",
            "neutral_history": "Neutral: as planned.",
            "history": "full risk debate",
            "judge_decision": "Proceed at half size.",
        },
        investment_plan="Accumulate on strength.",
        trader_investment_plan="Buy 1% of equity.",
        final_trade_decision="FINAL TRANSACTION PROPOSAL: **BUY**",
        recommended_action="BUY",
        trading_mode="investment",
        final_trade_intent={"decision_id": "decision-1", "symbol": "NVDA"},
    )
    state["report_context"] = build_report_context_index(state)
    state.update(overrides)
    return state


class AnalysisRecordTests(unittest.TestCase):
    def setUp(self):
        self.record = build_analysis_record(
            _final_state(), run_id="run-1", final_signal="BUY"
        )

    def test_the_record_is_keyed_to_the_decision_execution_will_use(self):
        self.assertEqual(self.record["decision_id"], "decision-1")
        self.assertEqual(self.record["run_id"], "run-1")
        self.assertEqual(self.record["symbol"], "NVDA")

    def test_the_gather_stage_carries_the_candidate_context(self):
        gather = self.record["gather"]

        self.assertEqual(gather["symbol"], "NVDA")
        self.assertEqual(gather["trade_date"], "2026-09-09")
        self.assertEqual(gather["current_position"], "NEUTRAL")

    def test_screener_provenance_is_carried_when_present(self):
        record = build_analysis_record(
            _final_state(candidate_provenance={"score": 9.1, "source": "screener"})
        )

        self.assertEqual(record["gather"]["provenance"]["score"], 9.1)

    def test_the_analyze_stage_counts_what_each_analyst_produced(self):
        analyze = self.record["analyze"]

        self.assertEqual(analyze["expected"], 5)
        self.assertEqual(analyze["produced"], 5)
        self.assertTrue(all(item["chars"] > 0 for item in analyze["reports"]))

    def test_an_analyst_that_produced_nothing_is_visible_as_such(self):
        record = build_analysis_record(_final_state(macro_report=""))

        macro = next(
            item
            for item in record["analyze"]["reports"]
            if item["report_key"] == "macro_report"
        )
        self.assertFalse(macro["produced"])
        self.assertEqual(record["analyze"]["produced"], 4)

    def test_the_compute_stage_carries_the_scored_matrix(self):
        compute = self.record["compute"]

        self.assertTrue(compute["available"])
        self.assertIn("scoreboard", compute)
        self.assertIn("sources", compute)

    def test_a_run_without_a_built_context_says_the_matrix_is_unavailable(self):
        state = _final_state()
        state.pop("report_context")

        self.assertFalse(build_analysis_record(state)["compute"]["available"])

    def test_an_unbuildable_matrix_is_reported_rather_than_raised(self):
        state = _final_state(report_context={"broken": True})

        self.assertFalse(build_analysis_record(state)["compute"]["available"])

    def test_the_decide_stage_carries_both_debates_and_their_rounds(self):
        decide = self.record["decide"]

        self.assertEqual(decide["research_debate"]["rounds"], 2)
        self.assertEqual(decide["risk_debate"]["rounds"], 3)
        self.assertIn("growth is accelerating", decide["research_debate"]["bull"]["text"])
        self.assertIn("size down", decide["risk_debate"]["safe"]["text"])

    def test_the_decide_stage_carries_both_verdicts(self):
        decide = self.record["decide"]

        self.assertEqual(decide["research_debate"]["verdict"], "Accumulate in thirds.")
        self.assertEqual(decide["risk_debate"]["verdict"], "Proceed at half size.")
        self.assertEqual(decide["recommended_action"], "BUY")

    def test_a_run_with_no_debates_still_records_a_shape(self):
        state = _final_state()
        state["investment_debate_state"] = {}
        state["risk_debate_state"] = {}

        decide = build_analysis_record(state)["decide"]

        self.assertEqual(decide["research_debate"]["rounds"], 0)
        self.assertEqual(decide["risk_debate"]["transcript"], "")


class AnalysisRecordPersistenceTests(unittest.TestCase):
    def _factory(self, journal=None, error=None):
        appended = []

        class Journal:
            def append(self, event_type, **kwargs):
                if error:
                    raise error
                appended.append((event_type, kwargs))
                return "ref"

        uow = mock.MagicMock()
        uow.__enter__ = lambda _self: SimpleNamespace(
            journal=journal or Journal(), commit=lambda: None
        )
        uow.__exit__ = lambda *a: False
        return (lambda: uow), appended

    def _record(self, record, factory):
        with mock.patch.object(
            analysis_record_module,
            "record_analysis_stages",
            analysis_record_module.record_analysis_stages,
        ):
            with mock.patch(
                "tradingagents.persistence.unit_of_work_factory", lambda: factory
            ):
                return record_analysis_stages(record)

    def test_the_record_is_appended_against_its_decision(self):
        factory, appended = self._factory()

        written = self._record(
            build_analysis_record(_final_state(), run_id="run-1"), factory
        )

        self.assertTrue(written)
        event_type, kwargs = appended[0]
        self.assertEqual(event_type, ANALYSIS_STAGES_EVENT)
        self.assertEqual(kwargs["decision_id"], "decision-1")
        self.assertEqual(kwargs["symbol"], "NVDA")
        self.assertIn("analysis", kwargs["payload"])

    def test_a_run_with_no_typed_intent_has_nothing_to_hang_it_on(self):
        """A HOLD that produced no intent is not an error."""
        factory, appended = self._factory()
        state = _final_state()
        state["final_trade_intent"] = {}

        written = self._record(build_analysis_record(state), factory)

        self.assertFalse(written)
        self.assertEqual(appended, [])

    def test_no_database_configured_is_not_an_error(self):
        with mock.patch(
            "tradingagents.persistence.unit_of_work_factory", lambda: None
        ):
            self.assertFalse(
                record_analysis_stages(build_analysis_record(_final_state()))
            )

    def test_a_write_failure_does_not_propagate(self):
        """The analysis has already finished; losing its record is not worth
        failing the run for."""
        factory, _appended = self._factory(error=RuntimeError("database gone"))

        self.assertFalse(
            self._record(build_analysis_record(_final_state()), factory)
        )


if __name__ == "__main__":
    unittest.main()
