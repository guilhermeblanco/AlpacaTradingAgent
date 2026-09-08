from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import DBAPIError

from tradingagents.evaluation.models import EvaluationEpisode, EvaluationOutcome
from tradingagents.lifecycle.models import LifecycleStatus
from tradingagents.persistence.postgres import (
    Base,
    DatabaseSettings,
    PostgresUnitOfWork,
    create_database_engine,
    create_session_factory,
)
from tradingagents.persistence.postgres.models import DecisionEventRow, LifecycleRow


REPO_ROOT = Path(__file__).resolve().parents[1]


def _exercise_unit_of_work(session_factory) -> str:
    decision_id = f"decision-{uuid4()}"
    symbol = f"T{uuid4().hex[:8]}".upper()
    now = datetime.now(timezone.utc)
    episode = EvaluationEpisode(
        decision_id=decision_id,
        symbol=symbol,
        action="OPEN",
        decision_at=now,
        data_as_of=now - timedelta(minutes=1),
        reference_price=200.0,
        benchmark_symbol="SPY",
        benchmark_price=500.0,
        confidence=0.75,
        experiment_id="postgres-contract",
    )
    outcome = EvaluationOutcome(
        decision_id=decision_id,
        horizon="1d",
        outcome_at=now + timedelta(days=1),
        asset_price=202.0,
        benchmark_price=502.0,
        asset_return_pct=1.0,
        benchmark_return_pct=0.4,
        excess_return_pct=0.6,
        directionally_correct=True,
        estimated_cost_pct=0.05,
    )

    with PostgresUnitOfWork(
        session_factory,
        admission_options={"cooldown_seconds": 60, "daily_token_budget": 1000},
    ) as uow:
        record = uow.lifecycle.create(
            decision_id=decision_id,
            symbol=symbol,
            idempotency_key=f"intent:{decision_id}",
            valid_until=now + timedelta(hours=1),
        )
        assert record.status is LifecycleStatus.RECEIVED
        event_id = uow.journal.append(
            "decision.received",
            symbol=symbol,
            decision_id=decision_id,
            payload={"action": "OPEN"},
        )
        uow.evaluation.record_episode(episode)
        uow.evaluation.record_outcome(outcome)
        assert uow.evaluation.pending_episodes(
            horizon="1d", due_before=now + timedelta(days=2)
        ) == []
        assert decision_id in [
            row.decision_id
            for row in uow.evaluation.pending_episodes(
                horizon="5d", due_before=now + timedelta(days=6)
            )
        ]
        admission = uow.admission.try_admit(
            symbol, price=200.0, estimated_tokens=100, now=now
        )
        assert admission.allowed
        uow.admission.complete(symbol)
        uow.lifecycle.transition(decision_id, LifecycleStatus.VALIDATED)
        uow.commit()

    with PostgresUnitOfWork(session_factory) as uow:
        stored = uow.lifecycle.get(decision_id)
        assert stored is not None
        assert stored.status is LifecycleStatus.VALIDATED
        assert uow.evaluation.get_episode(decision_id) is not None
        assert len(uow.evaluation.outcomes(experiment_id="postgres-contract")) >= 1
        uow.rollback()
    return event_id


def test_postgres_adapters_share_one_transaction_with_sqlite() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)

    event_id = _exercise_unit_of_work(session_factory)

    with session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(DecisionEventRow).where(
                DecisionEventRow.event_id == event_id
            )
        ) == 1


def test_unit_of_work_rolls_back_all_writes() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    decision_id = f"rollback-{uuid4()}"

    with pytest.raises(RuntimeError, match="force rollback"):
        with PostgresUnitOfWork(session_factory) as uow:
            uow.lifecycle.create(
                decision_id=decision_id,
                symbol="MSFT",
                idempotency_key=f"intent:{decision_id}",
            )
            uow.journal.append(
                "decision.received", symbol="MSFT", decision_id=decision_id
            )
            raise RuntimeError("force rollback")

    with session_factory() as session:
        assert session.get(LifecycleRow, decision_id) is None
        assert session.scalar(
            select(func.count()).select_from(DecisionEventRow)
        ) == 0


def test_database_settings_require_a_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="DATABASE_URL is required"):
        DatabaseSettings.from_env()


@pytest.fixture(scope="module")
def postgres_engine():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_database_engine(DatabaseSettings(url=database_url))
    try:
        yield engine
    finally:
        engine.dispose()


def test_initial_migration_creates_all_persistence_tables(postgres_engine) -> None:
    assert {
        "decision_events",
        "lifecycle",
        "lifecycle_transitions",
        "evaluation_episodes",
        "evaluation_outcomes",
        "analysis_admission",
        "outbox",
        "broker_orders",
        "broker_order_transitions",
        "broker_fills",
        "portfolio_reservations",
        "portfolio_reservation_allocations",
        "portfolio_reservation_transitions",
        "reconciliation_leases",
        "service_controls",
        "service_heartbeats",
        "integration_credentials",
        "integration_credential_audit",
    }.issubset(inspect(postgres_engine).get_table_names())


def test_postgres_unit_of_work_contract(postgres_engine) -> None:
    _exercise_unit_of_work(create_session_factory(postgres_engine))


def test_postgres_rejects_decision_event_mutation(postgres_engine) -> None:
    session_factory = create_session_factory(postgres_engine)
    event_id = _exercise_unit_of_work(session_factory)

    with postgres_engine.connect() as connection:
        transaction = connection.begin()
        with pytest.raises(DBAPIError, match="append-only"):
            connection.execute(
                text(
                    "UPDATE decision_events SET event_type = 'changed' "
                    "WHERE event_id = :event_id"
                ),
                {"event_id": event_id},
            )
        transaction.rollback()
