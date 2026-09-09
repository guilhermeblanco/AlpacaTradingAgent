from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select

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
from tradingagents.execution.dry_run_gateway import DryRunExecutionGateway
from tradingagents.execution.journal import ExecutionJournal
from tradingagents.execution.persistence import ExecutionPersistence
from tradingagents.execution.pipeline import ExecutionPipeline
from tradingagents.lifecycle import LifecycleStatus
from tradingagents.persistence.postgres import (
    Base,
    PostgresUnitOfWork,
    create_session_factory,
)
from tradingagents.persistence.postgres.models import DecisionEventRow, LifecycleRow
from tradingagents.persistence.runtime import build_persistence_runtime
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


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


def test_pipeline_records_lifecycle_and_events_through_unit_of_work(
    session_factory, tmp_path: Path
) -> None:
    intent = buy_intent()
    factory = lambda: PostgresUnitOfWork(session_factory)
    pipeline = ExecutionPipeline(
        FakeProvider(),
        DryRunExecutionGateway(),
        journal=ExecutionJournal(tmp_path),
        unit_of_work_factory=factory,
        safety_guard=SafetyGuard(
            {"safety_enabled": False},
            state_path=tmp_path / "safety.json",
            kill_switch_path=tmp_path / "KILL_SWITCH",
        ),
    )

    first = pipeline.execute("AAPL", intent, 1_000)
    second = pipeline.execute("AAPL", intent, 1_000)

    assert first["success"]
    assert second == first
    assert not list(tmp_path.rglob("*.jsonl"))
    with session_factory() as session:
        lifecycle = session.get(LifecycleRow, intent.decision_id)
        assert lifecycle.status == LifecycleStatus.SUCCEEDED.value
        events = session.scalars(
            select(DecisionEventRow)
            .where(DecisionEventRow.aggregate_id == intent.decision_id)
            .order_by(DecisionEventRow.aggregate_version)
        ).all()
        assert [event.aggregate_version for event in events] == list(
            range(1, len(events) + 1)
        )
        assert events[0].event_type == "intent_received"
        assert events[-1].event_type == "execution_completed"


def test_transition_rolls_back_when_event_append_fails(
    session_factory, tmp_path: Path
) -> None:
    intent = buy_intent()
    with PostgresUnitOfWork(session_factory) as uow:
        uow.lifecycle.create(
            decision_id=intent.decision_id,
            symbol=intent.symbol,
            idempotency_key="rollback-test",
        )
        uow.commit()

    class FailingUnitOfWork(PostgresUnitOfWork):
        def __enter__(self):
            value = super().__enter__()

            def fail(*args, **kwargs):
                raise RuntimeError("journal unavailable")

            self.journal.append = fail
            return value

    persistence = ExecutionPersistence(
        ExecutionJournal(tmp_path),
        unit_of_work_factory=lambda: FailingUnitOfWork(session_factory),
    )
    with pytest.raises(RuntimeError, match="journal unavailable"):
        persistence.record(
            "intent_validated",
            decision_id=intent.decision_id,
            symbol=intent.symbol,
            run_id=None,
            status=LifecycleStatus.VALIDATED,
        )

    with session_factory() as session:
        assert (
            session.get(LifecycleRow, intent.decision_id).status
            == LifecycleStatus.RECEIVED.value
        )


def test_pipeline_fails_closed_when_execution_scope_is_quarantined(
    session_factory, tmp_path: Path
) -> None:
    with PostgresUnitOfWork(session_factory) as uow:
        uow.operations.set_paused(
            "execution:alpaca:test",
            paused=True,
            reason="account position drift",
            updated_by="test",
        )
        uow.commit()

    class FailingProvider(FakeProvider):
        def get_portfolio_snapshot(self):
            raise AssertionError("quarantine must block before broker access")

    result = ExecutionPipeline(
        FailingProvider(),
        DryRunExecutionGateway(),
        journal=ExecutionJournal(tmp_path),
        unit_of_work_factory=lambda: PostgresUnitOfWork(session_factory),
        execution_control_service="execution:alpaca:test",
    ).execute("AAPL", buy_intent(), 1_000)

    assert not result["success"]
    assert "quarantined" in result["error"]
    assert result["validations"][0]["stage"] == "execution_quarantine"


def test_runtime_selects_local_and_rejects_unknown_backends() -> None:
    runtime = build_persistence_runtime({"persistence_backend": "local"})
    assert runtime.backend == "local"
    assert runtime.unit_of_work_factory is None

    with pytest.raises(ValueError, match="Unsupported persistence backend"):
        build_persistence_runtime({"persistence_backend": "mongodb"})
