import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tradingagents.agents.schemas import ExecutableAction, RiskDecision, build_trade_intent_from_risk_decision
from tradingagents.broker.models import AccountSnapshot, PortfolioSnapshot, QuoteSnapshot
from tradingagents.execution.dry_run_gateway import DryRunExecutionGateway
from tradingagents.execution.journal import ExecutionJournal
from tradingagents.execution.pipeline import ExecutionPipeline
from tradingagents.lifecycle import LifecycleRepository, LifecycleService, LifecycleStatus
from tradingagents.lifecycle.monitor import LifecycleMonitor
from tradingagents.safety import SafetyGuard


class FakeProvider:
    def get_portfolio_snapshot(self):
        return PortfolioSnapshot(account=AccountSnapshot(equity=100_000.0))

    def get_quote_snapshot(self, symbol):
        return QuoteSnapshot(symbol=symbol, bid_price=99.0, ask_price=101.0)


def buy_intent():
    return build_trade_intent_from_risk_decision(
        symbol="AAPL",
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


class TradeLifecycleTests(unittest.TestCase):
    def test_repository_persists_transitions_and_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = LifecycleRepository(Path(tmp) / "lifecycle.sqlite3")
            service = LifecycleService(repository)
            intent = buy_intent()

            record = service.begin(decision_id=intent.decision_id, symbol=intent.symbol)
            self.assertEqual(record.status, LifecycleStatus.RECEIVED)
            service.transition(intent.decision_id, LifecycleStatus.VALIDATED)
            record = service.transition(
                intent.decision_id,
                LifecycleStatus.SUCCEEDED,
                result={"success": True, "decision_id": intent.decision_id},
            )

            self.assertTrue(record.terminal)
            self.assertTrue(record.result["success"])
            self.assertTrue(record.idempotency_key.startswith("ata-"))

    def test_pipeline_returns_persisted_result_for_duplicate_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            intent = buy_intent()
            repository = LifecycleRepository(Path(tmp) / "lifecycle.sqlite3")
            pipeline = ExecutionPipeline(
                FakeProvider(),
                DryRunExecutionGateway(),
                journal=ExecutionJournal(tmp),
                lifecycle=LifecycleService(repository),
                safety_guard=SafetyGuard(
                    {"safety_enabled": False},
                    state_path=Path(tmp) / "safety.json",
                    kill_switch_path=Path(tmp) / "KILL_SWITCH",
                ),
            )

            first = pipeline.execute("AAPL", intent, 1_000)
            second = pipeline.execute("AAPL", intent, 1_000)

            self.assertTrue(first["success"])
            self.assertEqual(first, second)
            self.assertEqual(repository.get(intent.decision_id).status, LifecycleStatus.SUCCEEDED)
            self.assertIn("idempotency_key", first["plan"]["metadata"])

    def test_monitor_expires_due_intents_and_writes_heartbeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = datetime.now(timezone.utc)
            repository = LifecycleRepository(Path(tmp) / "lifecycle.sqlite3")
            service = LifecycleService(repository)
            intent = buy_intent()
            service.begin(
                decision_id=intent.decision_id,
                symbol=intent.symbol,
                valid_until=now - timedelta(seconds=1),
            )
            heartbeat = Path(tmp) / "heartbeat"

            expired = LifecycleMonitor(
                repository,
                heartbeat_path=heartbeat,
                clock=lambda: now,
            ).run_once()

            self.assertEqual(expired, 1)
            self.assertEqual(repository.get(intent.decision_id).status, LifecycleStatus.EXPIRED)
            self.assertEqual(heartbeat.read_text(), now.isoformat())


if __name__ == "__main__":
    unittest.main()
