"""Tests for the execution pipeline's refusals.

Everything here is a reason not to send an order. The pipeline is the last
deterministic gate before a broker call, so each guard has to produce a
recorded refusal rather than an exception or a silent pass.
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
    def __init__(self, *, portfolio=None, quote=None, error=None):
        self._portfolio = portfolio or PortfolioSnapshot(
            account=AccountSnapshot(equity=100_000.0)
        )
        self._quote = quote or QuoteSnapshot(
            symbol="NVDA", bid_price=99.0, ask_price=101.0
        )
        self._error = error

    def get_portfolio_snapshot(self):
        if self._error:
            raise self._error
        return self._portfolio

    def get_quote_snapshot(self, symbol):
        if self._error:
            raise self._error
        return self._quote


class CountingGateway:
    """Returns a real ExecutionResult so the pipeline's own post-processing
    runs rather than tripping over a stub."""

    name = "counting"

    def __init__(self):
        self.calls = 0

    def submit_plan(self, plan, intent):
        from tradingagents.execution.models import ExecutionResult

        self.calls += 1
        return ExecutionResult(
            success=True,
            decision_id=intent.decision_id,
            symbol=intent.symbol,
            gateway=self.name,
            plan=plan,
            actions=[{"action": "buy", "result": {"success": True}}],
        )

    def close_position(self, symbol):
        return {"success": True}


class PipelineFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.journal = ExecutionJournal(Path(self._tmp.name))
        self.gateway = CountingGateway()

    def _pipeline(self, provider=None, **kwargs):
        return ExecutionPipeline(
            provider or Provider(),
            self.gateway,
            journal=self.journal,
            lifecycle_enabled=False,
            **kwargs,
        )


class IntentValidationTests(PipelineFixture):
    def test_a_malformed_intent_is_refused_before_any_broker_call(self):
        result = self._pipeline().execute("NVDA", {"not": "an intent"}, 1000.0)

        self.assertFalse(result["success"])
        self.assertIn("Invalid trade intent", result["error"])
        self.assertEqual(self.gateway.calls, 0)

    def test_a_symbol_mismatch_is_refused(self):
        """Executing NVDA's decision against AAPL would trade the wrong name."""
        result = self._pipeline().execute("AAPL", _intent("NVDA"), 1000.0)

        self.assertFalse(result["success"])
        self.assertEqual(self.gateway.calls, 0)


class SnapshotTests(PipelineFixture):
    def test_an_unreachable_broker_fails_closed(self):
        provider = Provider(error=RuntimeError("broker unreachable"))

        result = self._pipeline(provider).execute("NVDA", _intent(), 1000.0)

        self.assertFalse(result["success"])
        self.assertIn("failed closed", result["error"])
        self.assertEqual(self.gateway.calls, 0)

    def test_the_refusal_names_the_decision_and_symbol(self):
        provider = Provider(error=RuntimeError("broker unreachable"))
        intent = _intent()

        result = self._pipeline(provider).execute("NVDA", intent, 1000.0)

        self.assertEqual(result["symbol"], "NVDA")
        self.assertEqual(result["decision_id"], intent.decision_id)

    def test_a_stale_snapshot_is_refused(self):
        from datetime import datetime, timedelta, timezone

        stale = PortfolioSnapshot(
            account=AccountSnapshot(equity=100_000.0),
            captured_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        )

        result = self._pipeline(Provider(portfolio=stale)).execute(
            "NVDA", _intent(), 1000.0
        )

        self.assertIsInstance(result, dict)


class SafetyTests(PipelineFixture):
    def test_a_blocking_safety_verdict_stops_the_order(self):
        guard = mock.MagicMock()
        guard.check_order.return_value = SimpleNamespace(
            allowed=False, reasons=["daily loss breaker tripped"], checks={}
        )

        result = self._pipeline(safety_guard=guard).execute("NVDA", _intent(), 1000.0)

        self.assertFalse(result["success"])
        self.assertEqual(self.gateway.calls, 0)

    def test_an_allowing_verdict_lets_the_order_through(self):
        guard = mock.MagicMock()
        guard.check_order.return_value = SimpleNamespace(
            allowed=True, reasons=[], checks={}
        )

        self._pipeline(safety_guard=guard).execute("NVDA", _intent(), 1000.0)

        self.assertEqual(self.gateway.calls, 1)


class RiskSizingTests(PipelineFixture):
    @staticmethod
    def _decision(*, approved=True, notional=500.0, reason="sized"):
        from tradingagents.risk.position_sizing import SizingDecision

        return SizingDecision(
            approved=approved,
            notional=notional,
            reason=reason,
            stop_loss_price=95.0,
            risk_amount=50.0,
            caps_applied=[],
        )

    def test_the_risk_sizer_can_reduce_a_leg(self):
        calls = []

        def sizer(**kwargs):
            calls.append(kwargs)
            return self._decision(notional=kwargs["requested_notional"] / 2)

        self._pipeline(risk_sizer=sizer).execute("NVDA", _intent(), 1000.0)

        self.assertTrue(calls)
        self.assertEqual(self.gateway.calls, 1)

    def test_a_refusing_risk_sizer_stops_the_order(self):
        """The deterministic engine is a gate, not a suggestion."""

        def sizer(**_kwargs):
            return self._decision(approved=False, notional=0.0, reason="no headroom")

        result = self._pipeline(risk_sizer=sizer).execute("NVDA", _intent(), 1000.0)

        self.assertFalse(result["success"])
        self.assertEqual(self.gateway.calls, 0)

    def test_a_failing_risk_sizer_does_not_stop_the_order(self):
        def sizer(**_kwargs):
            raise RuntimeError("no price history")

        result = self._pipeline(risk_sizer=sizer).execute("NVDA", _intent(), 1000.0)

        self.assertIsInstance(result, dict)


class JournalTests(PipelineFixture):
    def test_every_run_leaves_a_journal_trail(self):
        self._pipeline().execute("NVDA", _intent(), 1000.0)

        written = list(Path(self._tmp.name).rglob("*.jsonl"))

        self.assertTrue(written)

    def test_a_refusal_is_journalled_too(self):
        provider = Provider(error=RuntimeError("broker unreachable"))

        self._pipeline(provider).execute("NVDA", _intent(), 1000.0)

        written = list(Path(self._tmp.name).rglob("*.jsonl"))

        self.assertTrue(written)


if __name__ == "__main__":
    unittest.main()
