import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

from tradingagents.agents.schemas import (
    ExecutableAction,
    RiskDecision,
    build_trade_intent_from_risk_decision,
)
from tradingagents.broker.models import AccountSnapshot, PortfolioSnapshot, QuoteSnapshot
from tradingagents.broker.registry import BrokerCapabilities
from tradingagents.execution.dry_run_gateway import DryRunExecutionGateway
from tradingagents.execution.journal import ExecutionJournal
from tradingagents.execution.pipeline import ExecutionPipeline
from tradingagents.safety import SafetyGuard


class FakeProvider:
    def __init__(self, fail=False):
        self.fail = fail

    def get_portfolio_snapshot(self):
        if self.fail:
            raise ConnectionError("broker offline")
        return PortfolioSnapshot(account=AccountSnapshot(equity=100_000.0))

    def get_quote_snapshot(self, symbol):
        return QuoteSnapshot(symbol=symbol, bid_price=99.0, ask_price=101.0)


def buy_intent(symbol="AAPL"):
    return build_trade_intent_from_risk_decision(
        symbol=symbol,
        trading_mode="investment",
        current_position="NEUTRAL",
        decision=RiskDecision(
            action=ExecutableAction.BUY,
            confidence="high",
            risk_rationale="test",
            required_controls="test",
            target_portfolio_pct=5.0,
        ),
    )


class ExecutionPipelineTests(unittest.TestCase):
    def test_dry_run_never_calls_broker_and_writes_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = ExecutionPipeline(
                FakeProvider(),
                DryRunExecutionGateway(),
                journal=ExecutionJournal(tmp),
                safety_guard=SafetyGuard(
                    {"safety_enabled": False},
                    state_path=Path(tmp) / "safety.json",
                    kill_switch_path=Path(tmp) / "KILL_SWITCH",
                ),
            )
            result = pipeline.execute("AAPL", buy_intent(), 1_000)
            self.assertTrue(result["success"])
            self.assertEqual(result["gateway"], "dry-run")
            self.assertTrue(result["actions"][0]["simulated"])
            records = [json.loads(line) for line in Path(result["journal_path"]).read_text().splitlines()]
            self.assertEqual(records[0]["event_type"], "intent_received")
            self.assertEqual(records[-1]["event_type"], "execution_completed")

    def test_broker_outage_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = ExecutionPipeline(
                FakeProvider(fail=True),
                DryRunExecutionGateway(),
                journal=ExecutionJournal(tmp),
            ).execute("AAPL", buy_intent(), 1_000)
            self.assertFalse(result["success"])
            self.assertIn("failed closed", result["error"])

    def test_kill_switch_blocks_before_gateway(self):
        with tempfile.TemporaryDirectory() as tmp:
            guard = SafetyGuard(
                {"safety_enabled": True},
                state_path=Path(tmp) / "safety.json",
                kill_switch_path=Path(tmp) / "KILL_SWITCH",
            )
            guard.engage_kill_switch("test halt")
            result = ExecutionPipeline(
                FakeProvider(),
                DryRunExecutionGateway(),
                journal=ExecutionJournal(tmp),
                safety_guard=guard,
            ).execute("AAPL", buy_intent(), 1_000)
            self.assertFalse(result["success"])
            self.assertTrue(result["safety_blocked"])

    def test_crypto_short_is_rejected_before_snapshot(self):
        trade_intent = build_trade_intent_from_risk_decision(
            symbol="BTC/USD",
            trading_mode="trading",
            current_position="NEUTRAL",
            allow_shorts=True,
            decision=RiskDecision(
                action=ExecutableAction.SHORT,
                confidence="high",
                risk_rationale="test",
                required_controls="test",
                target_portfolio_pct=5.0,
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = ExecutionPipeline(
                FakeProvider(fail=True),
                DryRunExecutionGateway(),
                journal=ExecutionJournal(tmp),
            ).execute("BTC/USD", trade_intent, 1_000)
            self.assertFalse(result["success"])
            self.assertIn("Crypto short", result["error"])

    def test_broker_capabilities_reject_unsupported_asset_before_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = ExecutionPipeline(
                FakeProvider(fail=True),
                DryRunExecutionGateway(),
                journal=ExecutionJournal(tmp),
                broker_capabilities=BrokerCapabilities(crypto=False),
            ).execute("BTC/USD", buy_intent("BTC/USD"), 1_000)

            self.assertFalse(result["success"])
            self.assertIn("does not support crypto", result["error"])

    def test_broker_capabilities_reject_fractional_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = ExecutionPipeline(
                FakeProvider(),
                DryRunExecutionGateway(),
                journal=ExecutionJournal(tmp),
                broker_capabilities=BrokerCapabilities(
                    fractional_equities=False
                ),
            ).execute("AAPL", buy_intent(), 1_050)

            self.assertFalse(result["success"])
            self.assertIn("whole-share", result["error"])
            self.assertEqual(result["validations"][0]["stage"], "plan")

    def test_broker_without_native_protection_uses_software_mode(self):
        intent = build_trade_intent_from_risk_decision(
            symbol="AAPL",
            trading_mode="investment",
            current_position="NEUTRAL",
            decision=RiskDecision(
                action=ExecutableAction.BUY,
                confidence="high",
                risk_rationale="test",
                required_controls="Stop at $95",
                stop_loss="$95",
                target_portfolio_pct=5.0,
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = ExecutionPipeline(
                FakeProvider(),
                DryRunExecutionGateway(),
                journal=ExecutionJournal(tmp),
                broker_capabilities=BrokerCapabilities(native_brackets=False),
                safety_guard=SimpleNamespace(enabled=False),
            ).execute("AAPL", intent, 1_000)

            self.assertTrue(result["success"])
            self.assertEqual(result["plan"]["metadata"]["protection_mode"], "software")


if __name__ == "__main__":
    unittest.main()
