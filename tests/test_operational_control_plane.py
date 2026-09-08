from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine

from tradingagents.persistence.postgres import (
    Base,
    PostgresUnitOfWork,
    create_session_factory,
)


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


def test_pause_and_resume_are_shared_through_database(session_factory) -> None:
    now = datetime(2026, 9, 8, 15, tzinfo=timezone.utc)
    with PostgresUnitOfWork(session_factory) as uow:
        paused = uow.operations.set_paused(
            "autonomous-worker",
            paused=True,
            reason="operator review",
            updated_by="test",
            now=now,
        )
        uow.commit()
    assert paused.paused
    assert paused.reason == "operator review"

    with PostgresUnitOfWork(session_factory) as uow:
        assert uow.operations.is_paused("autonomous-worker")
        resumed = uow.operations.set_paused(
            "autonomous-worker", paused=False, updated_by="test", now=now
        )
        uow.commit()
    assert not resumed.paused
    assert resumed.reason is None


def test_health_marks_old_worker_heartbeats_stale(session_factory) -> None:
    now = datetime(2026, 9, 8, 15, tzinfo=timezone.utc)
    with PostgresUnitOfWork(session_factory) as uow:
        uow.operations.beat(
            "evaluation-worker",
            instance_id="worker-1",
            details={"resolved": 2},
            now=now,
        )
        uow.commit()
    with PostgresUnitOfWork(session_factory) as uow:
        health = uow.operations.health(
            now=now + timedelta(seconds=61), stale_after_seconds=60
        )
        uow.rollback()

    assert len(health.heartbeats) == 1
    assert health.heartbeats[0].stale
    assert health.heartbeats[0].details == {"resolved": 2}
    assert health.reconciliation_pending == 0
    assert health.active_reservation_allocations == 0
