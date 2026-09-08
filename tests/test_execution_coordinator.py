from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine

from tradingagents.agents.schemas import (
    ExecutableAction,
    IntentType,
    RiskDecision,
    TradeIntent,
    build_trade_intent_from_risk_decision,
)
from tradingagents.broker.models import AccountSnapshot, PortfolioSnapshot
from tradingagents.orchestration import (
    AllocationDispatchStatus,
    ReservationAwareExecutionCoordinator,
)
from tradingagents.persistence.postgres import (
    Base,
    PostgresUnitOfWork,
    create_session_factory,
)
from tradingagents.portfolio import (
    AllocationReservationState,
    PortfolioLimitsConfig,
    ReservationStatus,
)
from tradingagents.portfolio.batch import PortfolioIntentRequest, allocate_intent_batch


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


def _intent(symbol: str) -> TradeIntent:
    return build_trade_intent_from_risk_decision(
        symbol=symbol,
        trading_mode="investment",
        current_position="NEUTRAL",
        decision=RiskDecision(
            action=ExecutableAction.BUY,
            intent_type=IntentType.OPEN,
            confidence="high",
            confidence_score=0.8,
            risk_rationale="coordinator test",
            required_controls="test",
        ),
    )


def _batch(intents: list[TradeIntent]):
    return allocate_intent_batch(
        [
            PortfolioIntentRequest(
                intent=intent,
                requested_notional_usd=15_000,
                candidate_score=float(len(intents) - index),
            )
            for index, intent in enumerate(intents)
        ],
        PortfolioSnapshot(
            account=AccountSnapshot(equity=100_000),
            positions=[],
            captured_at="2026-09-08T15:00:00+00:00",
        ),
        {},
        limits=PortfolioLimitsConfig(
            vol_sizing_enabled=False, max_gross_exposure_pct=20
        ),
        max_symbol_concentration_pct=100,
    )


def test_coordinator_consumes_only_remote_accepted_allocations(session_factory) -> None:
    intents = [_intent("MSFT"), _intent("AAPL")]
    batch = _batch(intents)

    def executor(symbol, intent, notional, *, run_id):
        if symbol == "MSFT":
            return {
                "success": True,
                "actions": [{"result": {"order_id": "remote-1"}}],
            }
        return {"success": False, "error": "broker rejected", "actions": []}

    result = ReservationAwareExecutionCoordinator(
        lambda: PostgresUnitOfWork(session_factory),
        executor,
        worker_id="worker-a",
    ).execute(
        batch,
        {intent.decision_id: intent for intent in intents},
        account_key="tradier:test",
    )

    assert [row.status for row in result.allocations] == [
        AllocationDispatchStatus.ACCEPTED,
        AllocationDispatchStatus.RELEASED,
    ]
    with PostgresUnitOfWork(session_factory) as uow:
        reservation = uow.portfolio_reservations.reserve(
            batch, account_key="tradier:test"
        )
        uow.rollback()
    assert reservation.status is ReservationStatus.CONSUMED
    assert reservation.allocation_states == {
        intents[0].decision_id: AllocationReservationState.CONSUMED,
        intents[1].decision_id: AllocationReservationState.RELEASED,
    }

    next_intent = _intent("GOOG")
    next_result = ReservationAwareExecutionCoordinator(
        lambda: PostgresUnitOfWork(session_factory),
        lambda *args, **kwargs: {"success": False},
    ).execute(
        _batch([next_intent]),
        {next_intent.decision_id: next_intent},
        account_key="tradier:test",
    )
    assert next_result.allocations[0].approved_notional_usd == 5_000


def test_duplicate_dispatch_does_not_call_broker_twice(session_factory) -> None:
    intent = _intent("MSFT")
    batch = _batch([intent])
    calls = []

    def executor(*args, **kwargs):
        calls.append(args)
        return {"success": True, "actions": [{"result": {"order_id": "one"}}]}

    coordinator = ReservationAwareExecutionCoordinator(
        lambda: PostgresUnitOfWork(session_factory), executor, worker_id="worker-a"
    )
    first = coordinator.execute(
        batch, {intent.decision_id: intent}, account_key="robinhood:test"
    )
    second = coordinator.execute(
        batch, {intent.decision_id: intent}, account_key="robinhood:test"
    )

    assert first.allocations[0].status is AllocationDispatchStatus.ACCEPTED
    assert second.allocations[0].status is AllocationDispatchStatus.SKIPPED
    assert len(calls) == 1


def test_allocation_claim_can_only_be_taken_after_lease_expiry(session_factory) -> None:
    now = datetime.now(timezone.utc)
    intent = _intent("MSFT")
    batch = _batch([intent])
    with PostgresUnitOfWork(session_factory) as uow:
        reservation = uow.portfolio_reservations.reserve(
            batch, account_key="alpaca:test", now=now
        )
        assert uow.portfolio_reservations.claim_allocation(
            reservation.reservation_id,
            decision_id=intent.decision_id,
            worker_id="worker-a",
            lease_seconds=30,
            now=now,
        )
        uow.commit()
    with PostgresUnitOfWork(session_factory) as uow:
        assert not uow.portfolio_reservations.claim_allocation(
            reservation.reservation_id,
            decision_id=intent.decision_id,
            worker_id="worker-b",
            now=now + timedelta(seconds=29),
        )
        assert uow.portfolio_reservations.claim_allocation(
            reservation.reservation_id,
            decision_id=intent.decision_id,
            worker_id="worker-b",
            now=now + timedelta(seconds=30),
        )
        uow.rollback()
