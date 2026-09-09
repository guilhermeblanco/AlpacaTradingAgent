"""Tests for the Decision Tape.

A decision's record was split three ways — analysis in a run log, execution
in Postgres, outcomes in the evaluation ledger — so the UI could show what a
trade did but never what it was made of. The tape joins them on decision_id
and reports each of the seven stages as reached, skipped, or stopped.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from tradingagents.execution.gates import GateLedger
from tradingagents.workbench.tape import (
    STAGES,
    DecisionTape,
    StageState,
    build_tape,
    stage_for_event,
)


def _summary(**overrides):
    fields = {
        "decision_id": "decision-1",
        "symbol": "NVDA",
        "status": "succeeded",
        "created_at": datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 9, 9, 14, 5, tzinfo=timezone.utc),
        "broker": "alpaca",
        "order_count": 1,
        "filled_quantity": 10.0,
        "error": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _analysis(**overrides):
    analysis = {
        "run_id": "run-1",
        "decision_id": "decision-1",
        "symbol": "NVDA",
        "trade_date": "2026-09-09",
        "final_signal": "BUY",
        "gather": {
            "symbol": "NVDA",
            "trade_date": "2026-09-09",
            "current_position": "NEUTRAL",
            "provenance": {"score": 9.1, "source": "screener"},
        },
        "analyze": {
            "produced": 5,
            "expected": 5,
            "reports": [
                {"report_key": "market_report", "label": "Market", "produced": True, "chars": 400}
            ],
        },
        "compute": {
            "available": True,
            "scoreboard": {
                "net_direction": "bullish",
                "net_confidence": "medium",
                "bullish_score": 0.62,
                "bearish_score": 0.11,
                "freshness_score": 0.55,
                "quantitative_score": 0.3,
                "contradiction_score": 0.05,
                "source_diversity_score": 0.8,
            },
            "sources": [
                {
                    "label": "Market",
                    "claims": [
                        {
                            "claim_id": "market_001",
                            "claim": "Momentum is positive.",
                            "direction": "bullish",
                            "confidence": 0.47,
                            "freshness": 0.62,
                            "numeric_support": 0.0,
                            "contradiction": 0.0,
                        }
                    ],
                },
                {
                    "label": "News",
                    "claims": [
                        {
                            "claim_id": "news_001",
                            "claim": "No adverse headlines.",
                            "direction": "mixed",
                            "confidence": 0.8,
                            "freshness": 0.5,
                            "numeric_support": 0.1,
                            "contradiction": 0.0,
                        }
                    ],
                },
            ],
            "totals": {"claims": 2, "bullish": 1, "bearish": 0, "mixed": 1},
        },
        "decide": {
            "research_debate": {"rounds": 2, "verdict": "Accumulate."},
            "risk_debate": {"rounds": 3, "verdict": "Half size."},
            "final_decision": "FINAL TRANSACTION PROPOSAL: **BUY**",
            "recommended_action": "BUY",
        },
    }
    analysis.update(overrides)
    return analysis


def _ledger(**overrides):
    ledger = GateLedger(
        decision_id=overrides.pop("decision_id", "decision-1"),
        symbol="NVDA",
        requested_notional=overrides.pop("requested", 1_000.0),
    )
    return ledger


def _order(**overrides):
    fields = {
        "broker_order_id": "order-1",
        "side": "buy",
        "status": "filled",
        "filled_quantity": 10.0,
        "filled_avg_price": 100.0,
    }
    fields.update(overrides)
    return fields


def _outcome(horizon="1d", excess=1.5, correct=True):
    return {
        "horizon": horizon,
        "excess_return_pct": excess,
        "directionally_correct": correct,
    }


class StageAssemblyTests(unittest.TestCase):
    def test_every_pipeline_stage_appears_in_order(self):
        tape = build_tape(summary=_summary())

        self.assertEqual(
            [stage.key for stage in tape.stages], [key for key, _ in STAGES]
        )

    def test_a_run_with_no_analysis_has_four_pending_stages(self):
        tape = build_tape(summary=_summary())

        self.assertTrue(
            all(
                tape.stage(key).state is StageState.PENDING
                for key in ("gather", "analyze", "compute", "decide")
            )
        )

    def test_the_analysis_stages_report_what_the_graph_recorded(self):
        tape = build_tape(summary=_summary(), analysis=_analysis())

        self.assertEqual(tape.stage("gather").state, StageState.DONE)
        self.assertIn("5 of 5 analysts", tape.stage("analyze").headline)
        self.assertIn("2 claims scored", tape.stage("compute").headline)
        self.assertIn("BUY", tape.stage("decide").headline)

    def test_an_analysis_that_produced_no_reports_reads_as_skipped(self):
        analysis = _analysis(analyze={"produced": 0, "expected": 5, "reports": []})

        tape = build_tape(summary=_summary(), analysis=analysis)

        self.assertEqual(tape.stage("analyze").state, StageState.SKIPPED)

    def test_an_unbuilt_evidence_index_says_so(self):
        analysis = _analysis(compute={"available": False})

        tape = build_tape(summary=_summary(), analysis=analysis)

        self.assertEqual(tape.stage("compute").state, StageState.SKIPPED)
        self.assertIn("No evidence index", tape.stage("compute").headline)

    def test_the_run_metadata_is_carried_onto_the_tape(self):
        tape = build_tape(summary=_summary(), analysis=_analysis())

        self.assertEqual(tape.run_id, "run-1")
        self.assertEqual(tape.trade_date, "2026-09-09")
        self.assertEqual(tape.final_signal, "BUY")


class PrepareStageTests(unittest.TestCase):
    def test_a_cleared_ledger_marks_prepare_done_with_its_summary(self):
        ledger = _ledger()
        ledger.passed("intent")

        tape = build_tape(summary=_summary(), gate_ledger=ledger)

        self.assertEqual(tape.stage("prepare").state, StageState.DONE)
        self.assertIn("$1,000", tape.stage("prepare").headline)

    def test_a_blocking_gate_marks_prepare_blocked_and_names_it(self):
        ledger = _ledger()
        ledger.blocked("safety", reasons=["daily loss breaker tripped"])

        tape = build_tape(summary=_summary(status="blocked"), gate_ledger=ledger)

        self.assertEqual(tape.stage("prepare").state, StageState.BLOCKED)
        self.assertIn("daily loss breaker", tape.stage("prepare").headline)

    def test_a_submission_rejection_does_not_blame_the_prepare_stage(self):
        """The order was prepared correctly; the broker refused it."""
        ledger = _ledger()
        ledger.passed("safety")
        ledger.blocked("submission", reasons=["insufficient buying power"])

        tape = build_tape(summary=_summary(status="failed"), gate_ledger=ledger)

        self.assertEqual(tape.stage("prepare").state, StageState.DONE)
        self.assertEqual(tape.stage("order").state, StageState.FAILED)
        self.assertIn("insufficient buying power", tape.stage("order").headline)

    def test_no_ledger_leaves_prepare_pending(self):
        tape = build_tape(summary=_summary())

        self.assertEqual(tape.stage("prepare").state, StageState.PENDING)


class OrderStageTests(unittest.TestCase):
    def test_filled_orders_are_summarized(self):
        tape = build_tape(summary=_summary(), orders=[_order()])

        self.assertEqual(tape.stage("order").state, StageState.DONE)
        self.assertIn("10 filled", tape.stage("order").headline)

    def test_a_working_order_is_distinguished_from_a_filled_one(self):
        tape = build_tape(
            summary=_summary(), orders=[_order(status="new", filled_quantity=0.0)]
        )

        self.assertIn("working", tape.stage("order").headline)

    def test_a_decision_blocked_upstream_never_reached_the_broker(self):
        ledger = _ledger()
        ledger.blocked("safety", reasons=["breaker"])

        tape = build_tape(summary=_summary(status="blocked"), gate_ledger=ledger)

        self.assertEqual(tape.stage("order").state, StageState.SKIPPED)
        self.assertEqual(tape.stage("order").headline, "Never submitted")


class ReactStageTests(unittest.TestCase):
    def test_resolved_horizons_are_summarized(self):
        tape = build_tape(
            summary=_summary(),
            outcomes=[_outcome("1d", 1.5), _outcome("5d", -0.4, correct=False)],
        )

        headline = tape.stage("react").headline
        self.assertIn("2 horizon(s)", headline)
        self.assertIn("1 directionally correct", headline)
        self.assertIn("+1.50%", headline)

    def test_an_unresolved_decision_leaves_react_pending(self):
        tape = build_tape(summary=_summary())

        self.assertEqual(tape.stage("react").state, StageState.PENDING)


class ProgressTests(unittest.TestCase):
    def test_the_current_stage_is_the_furthest_one_reached(self):
        ledger = _ledger()
        ledger.passed("intent")

        tape = build_tape(
            summary=_summary(), analysis=_analysis(), gate_ledger=ledger
        )

        self.assertEqual(tape.current_stage, "prepare")

    def test_a_decision_with_nothing_recorded_sits_at_the_first_stage(self):
        self.assertEqual(build_tape(summary=_summary()).current_stage, "gather")

    def test_the_halting_stage_is_reported(self):
        ledger = _ledger()
        ledger.blocked("risk_sizing", reasons=["no headroom"])

        tape = build_tape(summary=_summary(status="blocked"), gate_ledger=ledger)

        self.assertEqual(tape.halted_at, "prepare")
        self.assertTrue(tape.is_terminal)

    def test_a_moving_decision_reports_no_halt(self):
        tape = build_tape(summary=_summary(status="planned"), analysis=_analysis())

        self.assertIsNone(tape.halted_at)
        self.assertFalse(tape.is_terminal)

    def test_a_terminal_lifecycle_status_counts_as_finished(self):
        for status in ("succeeded", "failed", "expired", "cancelled"):
            tape = build_tape(summary=_summary(status=status))

            self.assertTrue(tape.is_terminal, status)


class ChartDataTests(unittest.TestCase):
    def test_the_scoreboard_becomes_chartable_bars(self):
        tape = build_tape(summary=_summary(), analysis=_analysis())

        bars = tape.evidence_bars()

        self.assertEqual(
            [bar["label"] for bar in bars][:2], ["Bullish", "Bearish"]
        )
        self.assertEqual(bars[0]["value"], 0.62)

    def test_a_decision_with_no_evidence_charts_nothing(self):
        self.assertEqual(build_tape(summary=_summary()).evidence_bars(), [])

    def test_claims_are_flattened_best_scored_first(self):
        tape = build_tape(summary=_summary(), analysis=_analysis())

        rows = tape.claim_rows()

        self.assertEqual([row["claim_id"] for row in rows], ["news_001", "market_001"])
        self.assertEqual(rows[0]["source"], "News")

    def test_a_decision_with_no_claims_has_no_rows(self):
        self.assertEqual(build_tape(summary=_summary()).claim_rows(), [])


class EventStageTests(unittest.TestCase):
    def test_execution_events_land_on_the_prepare_stage(self):
        for event in ("snapshot_loaded", "risk_blocked", "gate_ledger_recorded"):
            self.assertEqual(stage_for_event("decision", event), "prepare")

    def test_broker_events_land_on_the_order_stage(self):
        self.assertEqual(stage_for_event("decision", "broker_submitted"), "order")
        self.assertEqual(stage_for_event("order", "anything"), "order")
        self.assertEqual(stage_for_event("fill", "anything"), "order")

    def test_outcome_events_land_on_the_react_stage(self):
        self.assertEqual(stage_for_event("outcome", "1d outcome"), "react")

    def test_the_analysis_record_lands_on_the_decide_stage(self):
        self.assertEqual(
            stage_for_event("decision", "analysis_stages_recorded"), "decide"
        )

    def test_an_unrecognized_event_defaults_to_prepare(self):
        self.assertEqual(stage_for_event("decision", "something_new"), "prepare")


class SerializationTests(unittest.TestCase):
    def test_a_tape_round_trips_through_json(self):
        ledger = _ledger()
        ledger.clipped("risk_sizing", notional_before=1_000.0, notional_after=600.0)
        tape = build_tape(
            summary=_summary(),
            analysis=_analysis(),
            gate_ledger=ledger,
            orders=[_order()],
            outcomes=[_outcome()],
        )

        restored = DecisionTape.model_validate(tape.model_dump(mode="json"))

        self.assertEqual(restored.decision_id, "decision-1")
        self.assertEqual(restored.gate_ledger.final_notional, 600.0)
        self.assertEqual(restored.current_stage, "react")


if __name__ == "__main__":
    unittest.main()
