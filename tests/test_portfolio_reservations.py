from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from tradingagents.agents.schemas import (
    ExecutableAction,
    IntentType,
    RiskDecision,
    build_trade_intent_from_risk_decision,
)
from tradingagents.broker.models import AccountSnapshot, PortfolioSnapshot, PositionSnapshot
from tradingagents.persistence.postgres import (
    Base,
    DatabaseSettings,
    PostgresUnitOfWork,
    create_database_engine,
    create_session_factory,
)
from tradingagents.portfolio import PortfolioLimitsConfig, ReservationStatus
from tradingagents.portfolio.batch import PortfolioIntentRequest, allocate_intent_batch


REPO_ROOT = Path(__file__).resolve().parents[1]


def _batch(requested: float = 15_000):
    intent = build_trade_intent_from_risk_decision(
        symbol="MSFT",
        trading_mode="investment",
        current_position="NEUTRAL",
        decision=RiskDecision(
            action=ExecutableAction.BUY,
            intent_type=IntentType.OPEN,
            confidence="high",
            confidence_score=0.8,
            risk_rationale="reservation test",
            required_controls="test",
        ),
    )
    snapshot = PortfolioSnapshot(
        account=AccountSnapshot(equity=100_000),
        positions=[PositionSnapshot(symbol="AAPL", quantity=1, market_value=80_000)],
        captured_at="2026-09-07T15:00:00+00:00",
    )
    return allocate_intent_batch(
        [PortfolioIntentRequest(intent=intent, requested_notional_usd=requested)],
        snapshot,
        {},
        limits=PortfolioLimitsConfig(
            vol_sizing_enabled=False, max_gross_exposure_pct=100
        ),
        max_symbol_concentration_pct=100,
    )


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


def _reserve(session_factory, batch, now, ttl=300, account_key="alpaca:paper-1"):
    with PostgresUnitOfWork(session_factory) as uow:
        reservation = uow.portfolio_reservations.reserve(
            batch, account_key=account_key, ttl_seconds=ttl, now=now
        )
        uow.commit()
        return reservation


def test_reservations_clip_later_batches_and_are_idempotent(session_factory) -> None:
    now = datetime.now(timezone.utc)
    first = _reserve(session_factory, _batch(), now)
    same = _reserve(session_factory, first.batch, now)
    second = _reserve(session_factory, _batch(), now)

    assert same.reservation_id == first.reservation_id
    assert first.reserved_notional_usd == 15_000
    assert second.reserved_notional_usd == 5_000
    assert second.batch.allocations[0].approved_notional_usd == 5_000
    assert "Outstanding portfolio or symbol reservations" in (
        second.batch.allocations[0].reasons[-1]
    )


def test_consumed_capacity_remains_reserved_until_release(session_factory) -> None:
    now = datetime.now(timezone.utc)
    first = _reserve(session_factory, _batch(), now)
    decision_id = first.batch.allocations[0].decision_id
    with PostgresUnitOfWork(session_factory) as uow:
        consumed = uow.portfolio_reservations.consume(
            first.reservation_id, decision_id=decision_id, now=now
        )
        uow.commit()
    assert consumed.status is ReservationStatus.CONSUMED
    with PostgresUnitOfWork(session_factory) as uow:
        repeated = uow.portfolio_reservations.consume(
            first.reservation_id, decision_id=decision_id, now=now
        )
        uow.commit()
    assert repeated.status is ReservationStatus.CONSUMED
    assert _reserve(session_factory, _batch(), now).reserved_notional_usd == 5_000

    with PostgresUnitOfWork(session_factory) as uow:
        released = uow.portfolio_reservations.release(first.reservation_id, now=now)
        uow.commit()
    assert released.status is ReservationStatus.RELEASED


def test_only_untouched_reservations_expire(session_factory) -> None:
    now = datetime.now(timezone.utc)
    first = _reserve(session_factory, _batch(), now, ttl=10)
    with PostgresUnitOfWork(session_factory) as uow:
        assert uow.portfolio_reservations.expire_due(now + timedelta(seconds=11)) == 1
        uow.commit()
    with PostgresUnitOfWork(session_factory) as uow:
        assert uow.portfolio_reservations.release(first.reservation_id).status is ReservationStatus.EXPIRED
        uow.rollback()


def test_reservations_share_symbol_concentration_across_batches(
    session_factory,
) -> None:
    now = datetime.now(timezone.utc)
    first = _reserve(session_factory, _batch(), now)
    assert first.reserved_notional_usd == 15_000

    second_batch = _batch()
    second_batch.max_symbol_concentration_pct = 20
    second_batch.account_equity_usd = 100_000
    second_batch.starting_symbol_exposure_usd = {"MSFT": 0}
    second = _reserve(session_factory, second_batch, now)

    assert second.reserved_notional_usd == 5_000
    assert "portfolio or symbol reservations" in second.batch.allocations[0].reasons[-1]


@pytest.fixture(scope="module")
def postgres_session_factory():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_database_engine(DatabaseSettings(url=database_url))
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


def test_postgres_account_lock_prevents_cross_batch_oversubscription(
    postgres_session_factory,
) -> None:
    now = datetime.now(timezone.utc)
    barrier = threading.Barrier(2)
    account_key = f"alpaca:{uuid4()}"

    def reserve(_):
        batch = _batch()
        barrier.wait(timeout=5)
        return _reserve(
            postgres_session_factory, batch, now, account_key=account_key
        ).reserved_notional_usd

    with ThreadPoolExecutor(max_workers=2) as executor:
        reserved = list(executor.map(reserve, range(2)))
    assert sorted(reserved) == [5_000, 15_000]
