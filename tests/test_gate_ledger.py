"""Tests for the execution gate ledger.

Every refusal in the pipeline is deliberate, but until the ledger the only
thing that survived was the error string of whichever gate stopped first.
The ledger keeps the whole sequence — the gates that passed, the numbers
they passed on, and the one that stopped it — so "why didn't this trade?"
answers itself.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tradingagents.agents.schemas import (
    ExecutableAction,
    RiskDecision,
    build_trade_intent_from_risk_decision,
)
from tradingagents.broker.models import (
    AccountSnapshot,
    PortfolioSnapshot,
    QuoteSnapshot,
)
from tradingagents.execution.gates import (
    GATE_SEQUENCE,
    GateLedger,
    GateStatus,
    ledger_from_payload,
)
from tradingagents.execution.journal import ExecutionJournal
from tradingagents.execution.pipeline import ExecutionPipeline


def _intent(symbol="NVDA", action=ExecutableAction.BUY):
    return build_trade_intent_from_risk_decision(
        symbol=symbol,
        trading_mode="investment",
        current_position="NEUTRAL",
        decision=RiskDecision(
            action=action,
            confidence="high",
            risk_rationale="test",
            required_controls="stop at 95",
            target_portfolio_pct=1.0,
        ),
        trade_date="2026-09-09",
    )


class Provider:
    def __init__(self, *, equity=100_000.0, error=None, captured_at=None):
        self._equity = equity
        self._error = error
        self._captured_at = captured_at

    def get_portfolio_snapshot(self):
        if self._error:
            raise self._error
        fields = {"account": AccountSnapshot(equity=self._equity)}
        if self._captured_at:
            fields["captured_at"] = self._captured_at
        return PortfolioSnapshot(**fields)

    def get_quote_snapshot(self, symbol):
        if self._error:
            raise self._error
        return QuoteSnapshot(symbol=symbol, bid_price=99.0, ask_price=101.0)


class Gateway:
    name = "test-gateway"

    def __init__(self, success=True):
        self.success = success
        self.calls = 0

    def submit_plan(self, plan, intent):
        from tradingagents.execution.models import ExecutionResult

        self.calls += 1
        return ExecutionResult(
            success=self.success,
            decision_id=intent.decision_id,
            symbol=intent.symbol,
            gateway=self.name,
            plan=plan,
            actions=[{"action": "buy", "result": {"success": self.success}}],
            error=None if self.success else "broker rejected",
        )

    def close_position(self, symbol):
        return {"success": True}


class LedgerModelTests(unittest.TestCase):
    def _ledger(self, requested=1_000.0):
        return GateLedger(
            decision_id="d1", symbol="NVDA", requested_notional=requested
        )

    def test_a_clean_run_is_allowed_and_names_no_blocker(self):
        ledger = self._ledger()
        ledger.passed("intent")

        self.assertTrue(ledger.allowed)
        self.assertIsNone(ledger.blocked_by)

    def test_the_first_blocking_gate_is_the_one_recorded(self):
        """A later gate cannot claim credit for a refusal already made."""
        ledger = self._ledger()
        ledger.blocked("snapshot", reasons=["stale"])
        ledger.blocked("safety", reasons=["daily loss"])

        self.assertEqual(ledger.blocked_by, "snapshot")
        self.assertFalse(ledger.allowed)

    def test_a_gate_is_retrievable_by_name(self):
        ledger = self._ledger()
        ledger.passed("intent", metrics={"legs": 1})

        self.assertEqual(ledger.gate("intent").metrics["legs"], 1)
        self.assertIsNone(ledger.gate("safety"))

    def test_a_clip_is_the_difference_between_before_and_after(self):
        ledger = self._ledger()
        outcome = ledger.clipped(
            "risk_sizing", notional_before=1_000.0, notional_after=600.0
        )

        self.assertEqual(outcome.clipped_by, 400.0)

    def test_a_gate_that_took_nothing_off_reports_no_clip(self):
        ledger = self._ledger()
        outcome = ledger.passed(
            "risk_sizing", notional_before=1_000.0, notional_after=1_000.0
        )

        self.assertIsNone(outcome.clipped_by)

    def test_the_final_notional_tracks_the_last_sizing_gate(self):
        ledger = self._ledger()
        ledger.passed("plan", notional_before=1_000.0, notional_after=1_000.0)
        ledger.clipped("risk_sizing", notional_before=1_000.0, notional_after=600.0)

        self.assertEqual(ledger.final_notional, 600.0)

    def test_every_gate_in_the_sequence_has_a_label(self):
        ledger = self._ledger()
        for name, label in GATE_SEQUENCE:
            self.assertEqual(ledger.passed(name).label, label)

    def test_an_unlisted_gate_still_gets_a_readable_label(self):
        self.assertEqual(self._ledger().passed("some_new_gate").label, "Some New Gate")


class WaterfallTests(unittest.TestCase):
    """The chart the workbench draws: requested, each clip, what was sent."""

    def _ledger(self):
        return GateLedger(
            decision_id="d1", symbol="NVDA", requested_notional=1_000.0
        )

    def test_an_unclipped_run_is_just_a_start_and_an_end(self):
        ledger = self._ledger()
        ledger.passed("intent")

        steps = ledger.waterfall()

        self.assertEqual([step["kind"] for step in steps], ["start", "end"])
        self.assertEqual(steps[-1]["running"], 1_000.0)

    def test_each_clip_becomes_a_negative_step(self):
        ledger = self._ledger()
        ledger.clipped("risk_sizing", notional_before=1_000.0, notional_after=600.0)
        ledger.clipped("safety", notional_before=600.0, notional_after=400.0)

        steps = ledger.waterfall()

        self.assertEqual(
            [step["kind"] for step in steps], ["start", "clip", "clip", "end"]
        )
        self.assertEqual([step["amount"] for step in steps[1:3]], [-400.0, -200.0])
        self.assertEqual(steps[-1]["running"], 400.0)

    def test_a_block_removes_whatever_was_left(self):
        ledger = self._ledger()
        ledger.clipped("risk_sizing", notional_before=1_000.0, notional_after=600.0)
        ledger.blocked("safety", reasons=["daily loss breaker"])

        steps = ledger.waterfall()

        self.assertEqual(steps[-2]["kind"], "blocked")
        self.assertEqual(steps[-2]["amount"], -600.0)
        self.assertEqual(steps[-1]["running"], 0.0)

    def test_nothing_is_charted_after_a_block(self):
        ledger = self._ledger()
        ledger.blocked("snapshot", reasons=["stale"])
        ledger.clipped("risk_sizing", notional_before=1_000.0, notional_after=600.0)

        self.assertEqual(len(ledger.waterfall()), 3)

    def test_a_blocking_step_carries_its_reasons(self):
        ledger = self._ledger()
        ledger.blocked("safety", reasons=["daily loss breaker"])

        self.assertEqual(
            ledger.waterfall()[1]["reasons"], ["daily loss breaker"]
        )


class SummaryTests(unittest.TestCase):
    def test_a_block_summary_names_the_gate_and_the_reason(self):
        ledger = GateLedger(decision_id="d1", symbol="NVDA", requested_notional=1_000.0)
        ledger.blocked("safety", reasons=["daily loss breaker tripped"])

        summary = ledger.summary()

        self.assertIn("Safety limits", summary)
        self.assertIn("daily loss breaker tripped", summary)

    def test_a_clipped_summary_states_both_numbers(self):
        ledger = GateLedger(decision_id="d1", symbol="NVDA", requested_notional=1_000.0)
        ledger.clipped("risk_sizing", notional_before=1_000.0, notional_after=600.0)

        summary = ledger.summary()

        self.assertIn("$1,000", summary)
        self.assertIn("$600", summary)

    def test_a_clean_summary_states_the_size(self):
        ledger = GateLedger(decision_id="d1", symbol="NVDA", requested_notional=1_000.0)
        ledger.passed("intent")

        self.assertIn("$1,000", ledger.summary())


class PayloadTests(unittest.TestCase):
    def test_a_persisted_ledger_round_trips(self):
        ledger = GateLedger(decision_id="d1", symbol="NVDA", requested_notional=1_000.0)
        ledger.clipped("risk_sizing", notional_before=1_000.0, notional_after=600.0)

        restored = ledger_from_payload({"gate_ledger": ledger.model_dump(mode="json")})

        self.assertEqual(restored.final_notional, 600.0)
        self.assertEqual(restored.gates[0].status, GateStatus.CLIPPED)

    def test_a_bare_ledger_document_is_accepted(self):
        ledger = GateLedger(decision_id="d1", symbol="NVDA", requested_notional=1_000.0)
        ledger.passed("intent")

        self.assertIsNotNone(ledger_from_payload(ledger.model_dump(mode="json")))

    def test_a_ledger_object_passes_through(self):
        ledger = GateLedger(decision_id="d1", symbol="NVDA", requested_notional=1_000.0)

        self.assertIs(ledger_from_payload(ledger), ledger)

    def test_anything_else_is_not_a_ledger(self):
        for payload in (None, "text", {}, {"gate_ledger": {"nope": 1}}):
            self.assertIsNone(ledger_from_payload(payload), payload)


class PipelineLedgerTests(unittest.TestCase):
    """The ledger the pipeline actually produces, gate by gate."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.gateway = Gateway()

    def _pipeline(self, provider=None, **kwargs):
        return ExecutionPipeline(
            provider or Provider(),
            kwargs.pop("gateway", None) or self.gateway,
            journal=ExecutionJournal(Path(self._tmp.name)),
            lifecycle_enabled=False,
            **kwargs,
        )

    def _ledger(self, result):
        restored = ledger_from_payload(result)
        self.assertIsNotNone(restored, result.get("error"))
        return restored

    def test_a_clean_run_records_every_gate_it_passed(self):
        result = self._pipeline().execute("NVDA", _intent(), 1_000.0)

        ledger = self._ledger(result)
        self.assertTrue(ledger.allowed)
        self.assertEqual(
            [gate.name for gate in ledger.gates][:4],
            ["execution_quarantine", "intent", "snapshot", "plan"],
        )
        self.assertEqual(ledger.gates[-1].name, "submission")

    def test_the_requested_size_is_the_starting_point(self):
        result = self._pipeline().execute("NVDA", _intent(), 2_500.0)

        self.assertEqual(self._ledger(result).requested_notional, 2_500.0)

    def test_a_symbol_mismatch_is_blocked_at_the_intent_gate(self):
        result = self._pipeline().execute("AAPL", _intent("NVDA"), 1_000.0)

        ledger = self._ledger(result)
        self.assertEqual(ledger.blocked_by, "intent")
        self.assertTrue(ledger.gate("intent").reasons)
        self.assertEqual(self.gateway.calls, 0)

    def test_an_unreachable_broker_is_blocked_at_the_snapshot_gate(self):
        provider = Provider(error=RuntimeError("credentials rejected"))

        result = self._pipeline(provider).execute("NVDA", _intent(), 1_000.0)

        ledger = self._ledger(result)
        self.assertEqual(ledger.blocked_by, "snapshot")
        self.assertIn("credentials rejected", " ".join(ledger.gate("snapshot").reasons))

    def test_the_snapshot_gate_records_what_it_saw(self):
        result = self._pipeline().execute("NVDA", _intent(), 1_000.0)

        metrics = self._ledger(result).gate("snapshot").metrics
        self.assertEqual(metrics["equity"], 100_000.0)
        self.assertEqual(metrics["reference_price"], 100.0)

    def test_a_blocking_safety_verdict_is_recorded_with_its_checks(self):
        guard = mock.MagicMock()
        guard.enabled = True
        guard.check_order.return_value = SimpleNamespace(
            allowed=False,
            reasons=["daily loss breaker tripped"],
            checks={"daily_loss_pct": -4.2},
        )

        result = self._pipeline(safety_guard=guard).execute("NVDA", _intent(), 1_000.0)

        ledger = self._ledger(result)
        self.assertEqual(ledger.blocked_by, "safety")
        self.assertEqual(ledger.gate("safety").metrics["daily_loss_pct"], -4.2)
        self.assertEqual(self.gateway.calls, 0)

    def test_a_disabled_safety_layer_is_recorded_as_skipped_not_passed(self):
        """"Off" and "checked and fine" are different states to read."""
        guard = mock.MagicMock()
        guard.enabled = False

        result = self._pipeline(safety_guard=guard).execute("NVDA", _intent(), 1_000.0)

        self.assertEqual(self._ledger(result).gate("safety").status, GateStatus.SKIPPED)

    def test_absent_risk_sizing_is_recorded_as_skipped(self):
        result = self._pipeline().execute("NVDA", _intent(), 1_000.0)

        self.assertEqual(
            self._ledger(result).gate("risk_sizing").status, GateStatus.SKIPPED
        )

    def _sizing(self, **overrides):
        from tradingagents.risk.position_sizing import SizingDecision

        fields = {
            "approved": True,
            "notional": 400.0,
            "reason": "sized",
            "stop_loss_price": 95.0,
            "risk_amount": 50.0,
            "caps_applied": ["per_trade_risk"],
        }
        fields.update(overrides)
        return SizingDecision(**fields)

    def test_a_reduced_size_is_recorded_as_a_clip_with_its_caps(self):
        result = self._pipeline(
            risk_sizer=lambda **kwargs: self._sizing()
        ).execute("NVDA", _intent(), 1_000.0)

        gate = self._ledger(result).gate("risk_sizing")
        self.assertEqual(gate.status, GateStatus.CLIPPED)
        self.assertEqual(gate.reasons, ["per_trade_risk"])
        self.assertEqual(gate.notional_after, 400.0)
        self.assertEqual(gate.metrics["stop_loss_price"], 95.0)

    def test_a_refusing_risk_sizer_blocks_at_its_own_gate(self):
        result = self._pipeline(
            risk_sizer=lambda **kwargs: self._sizing(
                approved=False, notional=0.0, reason="no headroom"
            )
        ).execute("NVDA", _intent(), 1_000.0)

        ledger = self._ledger(result)
        self.assertEqual(ledger.blocked_by, "risk_sizing")
        self.assertEqual(ledger.gate("risk_sizing").reasons, ["no headroom"])

    def test_a_broker_rejection_blocks_at_the_submission_gate(self):
        result = self._pipeline(gateway=Gateway(success=False)).execute(
            "NVDA", _intent(), 1_000.0
        )

        ledger = self._ledger(result)
        self.assertEqual(ledger.blocked_by, "submission")

    def test_the_waterfall_reaches_the_size_that_was_sent(self):
        result = self._pipeline(
            risk_sizer=lambda **kwargs: self._sizing()
        ).execute("NVDA", _intent(), 1_000.0)

        steps = self._ledger(result).waterfall()

        self.assertEqual(steps[0]["running"], 1_000.0)
        self.assertEqual(steps[-1]["running"], 400.0)

    def test_the_ledger_is_journalled_after_the_outcome_it_explains(self):
        import json

        result = self._pipeline().execute("NVDA", _intent(), 1_000.0)

        records = [
            json.loads(line)
            for line in Path(result["journal_path"]).read_text().splitlines()
        ]
        self.assertEqual(records[-1]["event_type"], "gate_ledger_recorded")
        self.assertTrue(records[-1]["payload"]["gate_ledger"]["gates"])

    def test_a_journal_failure_does_not_change_the_trade(self):
        """The order has already gone out by the time the ledger is written."""
        pipeline = self._pipeline()
        result = pipeline._execute("NVDA", _intent(), 1_000.0)

        with mock.patch.object(
            pipeline.persistence,
            "record",
            side_effect=RuntimeError("journal unavailable"),
        ):
            pipeline._record_gate_ledger(result, None)

        self.assertTrue(result["success"])

    def test_a_result_without_a_ledger_is_not_journalled(self):
        pipeline = self._pipeline()
        with mock.patch.object(pipeline.persistence, "record") as record:
            pipeline._record_gate_ledger({"success": False}, None)

        record.assert_not_called()

    def test_a_malformed_intent_never_reaches_a_gate(self):
        result = self._pipeline().execute("NVDA", {"not": "an intent"}, 1_000.0)

        self.assertFalse(result["success"])
        self.assertNotIn("gate_ledger", result)


if __name__ == "__main__":
    unittest.main()
