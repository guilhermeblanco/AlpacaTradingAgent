from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine

from tradingagents.agents.schemas import (
    ExecutableAction,
    IntentType,
    RiskDecision,
    build_trade_intent_from_risk_decision,
)
from tradingagents.broker.models import AccountSnapshot, PortfolioSnapshot
from tradingagents.orchestration import (
    AutonomousCycleScheduler,
    Candidate,
    MarketSessionGate,
    ReservationAwareExecutionCoordinator,
)
from tradingagents.evaluation import (
    DeterministicExperimentAssigner,
    ExperimentVariant,
)
from tradingagents.persistence.postgres import (
    Base,
    PostgresUnitOfWork,
    create_session_factory,
)
from tradingagents.portfolio import PortfolioLimitsConfig


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


class Snapshots:
    def get_portfolio_snapshot(self):
        return PortfolioSnapshot(
            account=AccountSnapshot(equity=100_000),
            positions=[],
            captured_at="2026-09-08T15:00:00+00:00",
            broker="test",
        )


def _intent(symbol):
    return build_trade_intent_from_risk_decision(
        symbol=symbol,
        trading_mode="investment",
        current_position="NEUTRAL",
        decision=RiskDecision(
            action=ExecutableAction.BUY,
            intent_type=IntentType.OPEN,
            confidence="high",
            confidence_score=0.9,
            risk_rationale="scheduler test",
            required_controls="test",
        ),
    )


def test_market_session_gate_uses_exchange_holidays_but_crypto_is_continuous() -> None:
    gate = MarketSessionGate()
    christmas = datetime(2026, 12, 25, 15, tzinfo=timezone.utc)

    assert not gate.is_open("equity", now=christmas)
    assert gate.is_open("crypto", now=christmas)


def test_cycle_filters_broker_capabilities_and_dispatches_admitted_candidate(
    session_factory,
) -> None:
    calls = []

    class AlwaysOpen:
        def is_open(self, asset_class, *, now):
            return True

    def execute(symbol, intent, notional, *, run_id):
        calls.append((symbol, notional, run_id))
        return {"success": True, "actions": [{"result": {"order_id": "remote"}}]}

    coordinator = ReservationAwareExecutionCoordinator(
        lambda: PostgresUnitOfWork(session_factory), execute
    )
    scheduler = AutonomousCycleScheduler(
        snapshot_provider=Snapshots(),
        candidate_source=lambda snapshot: [
            Candidate(symbol="BTC/USD", asset_class="crypto", score=10, source="test"),
            Candidate(
                symbol="MSFT",
                asset_class="stock",
                score=9,
                source="test",
                provenance={"price": 100},
            ),
        ],
        analysis_handler=lambda candidate: _intent(candidate.symbol),
        price_history_loader=lambda symbols: {},
        requested_notional=lambda candidate, intent: 1_000,
        execution_coordinator=coordinator,
        unit_of_work_factory=lambda: PostgresUnitOfWork(session_factory),
        account_key="tradier:test",
        analysis_provider="openai",
        allowed_asset_classes={"equity"},
        limits=PortfolioLimitsConfig(vol_sizing_enabled=False),
        orchestrator=None,
        session_gate=AlwaysOpen(),
    )

    result = scheduler.run_once(
        now=datetime(2026, 9, 8, 15, tzinfo=timezone.utc)
    )

    assert result.discovered == 2
    assert result.capability_blocked == 1
    assert result.admitted == 1
    assert not result.errors
    assert [row[0] for row in calls] == ["MSFT"]


def test_cycle_completes_admission_after_analysis_failure(session_factory) -> None:
    candidate = Candidate(symbol="MSFT", score=9, source="test")

    class AlwaysOpen:
        def is_open(self, asset_class, *, now):
            return True

    scheduler = AutonomousCycleScheduler(
        snapshot_provider=Snapshots(),
        candidate_source=lambda snapshot: [candidate],
        analysis_handler=lambda candidate: (_ for _ in ()).throw(RuntimeError("LLM down")),
        price_history_loader=lambda symbols: {},
        requested_notional=lambda candidate, intent: 1_000,
        execution_coordinator=ReservationAwareExecutionCoordinator(
            lambda: PostgresUnitOfWork(session_factory), lambda *args, **kwargs: {}
        ),
        unit_of_work_factory=lambda: PostgresUnitOfWork(session_factory),
        account_key="alpaca:test",
        analysis_provider="openai",
        allowed_asset_classes={"equity"},
        session_gate=AlwaysOpen(),
    )
    scheduler.run_once(now=datetime(2026, 9, 8, 15, tzinfo=timezone.utc))

    with PostgresUnitOfWork(session_factory) as uow:
        decision = uow.admission.try_admit(
            "MSFT", now=datetime(2026, 9, 9, 16, tzinfo=timezone.utc)
        )
        uow.rollback()
    assert decision.allowed


def test_shadow_experiment_is_recorded_but_never_dispatched(session_factory) -> None:
    calls = []
    recorded = []

    class AlwaysOpen:
        def is_open(self, asset_class, *, now):
            return True

    intent = _intent("MSFT")
    scheduler = AutonomousCycleScheduler(
        snapshot_provider=Snapshots(),
        candidate_source=lambda snapshot: [
            Candidate(symbol="MSFT", score=9, source="test")
        ],
        analysis_handler=lambda candidate: intent,
        price_history_loader=lambda symbols: {},
        requested_notional=lambda candidate, intent: 1_000,
        execution_coordinator=ReservationAwareExecutionCoordinator(
            lambda: PostgresUnitOfWork(session_factory),
            lambda *args, **kwargs: calls.append(args),
        ),
        unit_of_work_factory=lambda: PostgresUnitOfWork(session_factory),
        account_key="alpaca:test",
        analysis_provider="openai",
        allowed_asset_classes={"equity"},
        session_gate=AlwaysOpen(),
        experiment_assigner=DeterministicExperimentAssigner(
            [ExperimentVariant(experiment_id="challenger", execution_eligible=False)]
        ),
        shadow_episode_recorder=lambda intent, assignment: recorded.append(
            (intent, assignment)
        )
        or True,
    )

    result = scheduler.run_once(
        now=datetime(2026, 9, 8, 15, tzinfo=timezone.utc)
    )

    assert result.shadow_recorded == 1
    assert result.dispatch is None
    assert calls == []
    assert recorded[0][0].metadata["experiment_id"] == "challenger"
    allocation = result.portfolio_run.decision_batch.allocations[0]
    assert allocation.approved_notional_usd == 0
    assert "not execution eligible" in allocation.reasons[-1]
