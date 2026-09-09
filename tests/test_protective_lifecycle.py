import tempfile
import unittest
from types import SimpleNamespace

from tradingagents.agents.schemas import (
    ExecutableAction,
    RiskDecision,
    build_trade_intent_from_risk_decision,
)
from tradingagents.broker.models import (
    AccountSnapshot,
    PortfolioSnapshot,
    PositionSnapshot,
    QuoteSnapshot,
)
from tradingagents.broker.registry import BrokerCapabilities
from tradingagents.execution.journal import ExecutionJournal
from tradingagents.execution.models import ExecutionResult
from tradingagents.execution.pipeline import ExecutionPipeline


class Snapshots:
    def get_portfolio_snapshot(self):
        return PortfolioSnapshot(account=AccountSnapshot(equity=100_000))

    def get_quote_snapshot(self, symbol):
        return QuoteSnapshot(symbol=symbol, bid_price=99, ask_price=101)


class RemoteGateway:
    name = "tradier"

    def submit_plan(self, plan, intent):
        return ExecutionResult(
            success=True,
            decision_id=plan.decision_id,
            symbol=plan.symbol,
            gateway=self.name,
            plan=plan,
            actions=[{"action": "buy", "result": {"success": True, "order_id": "1"}}],
        )


class StopStore:
    calls = []

    @classmethod
    def add_virtual_stop(cls, **kwargs):
        cls.calls.append(kwargs)


def protected_intent():
    return build_trade_intent_from_risk_decision(
        symbol="AAPL",
        trading_mode="investment",
        current_position="NEUTRAL",
        decision=RiskDecision(
            action=ExecutableAction.BUY,
            confidence="high",
            risk_rationale="test",
            required_controls="Stop at 95",
            stop_loss="$95",
            target_portfolio_pct=5,
        ),
    )


class ProtectiveLifecycleTests(unittest.TestCase):
    def setUp(self):
        StopStore.calls = []

    def test_non_native_broker_registers_software_stop_after_submission(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = ExecutionPipeline(
                Snapshots(),
                RemoteGateway(),
                journal=ExecutionJournal(tmp),
                broker_capabilities=BrokerCapabilities(native_brackets=False),
                protective_stop_store=StopStore,
                safety_guard=SimpleNamespace(enabled=False),
            ).execute("AAPL", protected_intent(), 1_000)

        self.assertTrue(result["success"])
        self.assertEqual(result["plan"]["metadata"]["protection_mode"], "software")
        self.assertEqual(StopStore.calls[0]["broker"], "tradier")
        self.assertEqual(StopStore.calls[0]["stop_loss_price"], 95)

    def test_native_broker_does_not_register_software_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = ExecutionPipeline(
                Snapshots(),
                RemoteGateway(),
                journal=ExecutionJournal(tmp),
                broker_capabilities=BrokerCapabilities(
                    native_brackets=True, fractional_equities=True
                ),
                protective_stop_store=StopStore,
                safety_guard=SimpleNamespace(enabled=False),
            ).execute("AAPL", protected_intent(), 1_000)

        self.assertEqual(result["plan"]["metadata"]["protection_mode"], "native")
        self.assertEqual(StopStore.calls, [])


if __name__ == "__main__":
    unittest.main()
