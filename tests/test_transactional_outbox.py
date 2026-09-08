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
from sqlalchemy import create_engine, select

from tradingagents.persistence.outbox import OutboxDispatcher, OutboxLeaseLost
from tradingagents.persistence.postgres import (
    Base,
    DatabaseSettings,
    PostgresUnitOfWork,
    create_database_engine,
    create_session_factory,
)
from tradingagents.persistence.postgres.models import OutboxRow


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield create_session_factory(engine)
    engine.dispose()


def test_outbox_enqueue_commits_atomically_with_decision_state(session_factory) -> None:
    decision_id = f"decision-{uuid4()}"
    idempotency_key = f"broker:{decision_id}"
    with PostgresUnitOfWork(session_factory) as uow:
        uow.lifecycle.create(
            decision_id=decision_id,
            symbol="AAPL",
            idempotency_key=f"intent:{decision_id}",
        )
        outbox_id = uow.outbox.enqueue(
            "broker.submit_plan",
            idempotency_key=idempotency_key,
            payload={"decision_id": decision_id},
        )
        uow.commit()

    with session_factory() as session:
        row = session.get(OutboxRow, outbox_id)
        assert row is not None
        assert row.idempotency_key == idempotency_key

    rolled_back_id = f"rollback-{uuid4()}"
    with pytest.raises(RuntimeError):
        with PostgresUnitOfWork(session_factory) as uow:
            uow.lifecycle.create(
                decision_id=rolled_back_id,
                symbol="MSFT",
                idempotency_key=f"intent:{rolled_back_id}",
            )
            uow.outbox.enqueue(
                "broker.submit_plan",
                idempotency_key=f"broker:{rolled_back_id}",
                payload={"decision_id": rolled_back_id},
            )
            raise RuntimeError("rollback")

    with session_factory() as session:
        assert session.scalar(
            select(OutboxRow).where(
                OutboxRow.idempotency_key == f"broker:{rolled_back_id}"
            )
        ) is None


def test_enqueue_is_idempotent_and_rejects_key_reuse(session_factory) -> None:
    with PostgresUnitOfWork(session_factory) as uow:
        first = uow.outbox.enqueue(
            "broker.submit_plan", idempotency_key="same-key", payload={"leg": 1}
        )
        second = uow.outbox.enqueue(
            "broker.submit_plan", idempotency_key="same-key", payload={"leg": 1}
        )
        assert first == second
        with pytest.raises(ValueError, match="different content"):
            uow.outbox.enqueue(
                "broker.submit_plan",
                idempotency_key="same-key",
                payload={"leg": 2},
            )
        uow.commit()


def test_dispatcher_acknowledges_success_and_dead_letters_failure(
    session_factory,
) -> None:
    now = datetime.now(timezone.utc)
    delivered = []
    with PostgresUnitOfWork(session_factory) as uow:
        successful_id = uow.outbox.enqueue(
            "broker.submit_plan",
            idempotency_key="successful-message",
            payload={"decision_id": "one"},
            available_at=now,
        )
        failed_id = uow.outbox.enqueue(
            "unknown.topic",
            idempotency_key="failed-message",
            payload={"decision_id": "two"},
            available_at=now,
        )
        uow.commit()

    dispatcher = OutboxDispatcher(
        lambda: PostgresUnitOfWork(session_factory),
        {"broker.submit_plan": delivered.append},
        worker_id="worker-1",
        max_attempts=1,
        clock=lambda: now,
    )
    result = dispatcher.dispatch_once()

    assert result.model_dump() == {
        "claimed": 2,
        "processed": 1,
        "failed": 1,
        "dead_lettered": 1,
    }
    assert delivered[0].idempotency_key == "successful-message"
    with session_factory() as session:
        successful = session.get(OutboxRow, successful_id)
        failed = session.get(OutboxRow, failed_id)
        assert successful.processed_at is not None
        assert successful.locked_by is None
        assert failed.dead_lettered_at is not None
        assert "KeyError" in failed.last_error


def test_dispatcher_uses_bounded_exponential_backoff(session_factory) -> None:
    dispatcher = OutboxDispatcher(
        lambda: PostgresUnitOfWork(session_factory),
        {},
        worker_id="worker-1",
        base_retry_seconds=5,
        max_retry_seconds=20,
    )
    assert [dispatcher._retry_delay(attempt) for attempt in range(1, 6)] == [
        5,
        10,
        20,
        20,
        20,
    ]


def test_expired_lease_can_be_reclaimed_but_stale_worker_cannot_ack(
    session_factory,
) -> None:
    now = datetime.now(timezone.utc)
    with PostgresUnitOfWork(session_factory) as uow:
        outbox_id = uow.outbox.enqueue(
            "broker.submit_plan",
            idempotency_key="leased-message",
            payload={},
            available_at=now,
        )
        uow.commit()

    with PostgresUnitOfWork(session_factory) as uow:
        assert len(
            uow.outbox.claim(worker_id="worker-a", lease_seconds=10, now=now)
        ) == 1
        uow.commit()

    with PostgresUnitOfWork(session_factory) as uow:
        reclaimed = uow.outbox.claim(
            worker_id="worker-b", now=now + timedelta(seconds=11)
        )
        assert len(reclaimed) == 1
        uow.commit()

    with PostgresUnitOfWork(session_factory) as uow:
        with pytest.raises(OutboxLeaseLost):
            uow.outbox.mark_processed(outbox_id, worker_id="worker-a")
        uow.rollback()


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


def test_postgres_skip_locked_prevents_double_claim(postgres_session_factory) -> None:
    key = f"skip-locked-{uuid4()}"
    with PostgresUnitOfWork(postgres_session_factory) as uow:
        uow.outbox.enqueue("broker.submit_plan", idempotency_key=key, payload={})
        uow.commit()

    with PostgresUnitOfWork(postgres_session_factory) as first:
        claimed = first.outbox.claim(worker_id="worker-a")
        assert len(claimed) == 1
        with PostgresUnitOfWork(postgres_session_factory) as second:
            assert second.outbox.claim(worker_id="worker-b") == []
            second.rollback()
        first.rollback()


def test_postgres_concurrent_enqueue_converges_on_one_message(
    postgres_session_factory,
) -> None:
    key = f"concurrent-{uuid4()}"
    barrier = threading.Barrier(2)

    def enqueue() -> str:
        with PostgresUnitOfWork(postgres_session_factory) as uow:
            barrier.wait(timeout=5)
            outbox_id = uow.outbox.enqueue(
                "broker.submit_plan",
                idempotency_key=key,
                payload={"decision_id": key},
            )
            uow.commit()
            return outbox_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(lambda _: enqueue(), range(2)))

    assert ids[0] == ids[1]
    with postgres_session_factory() as session:
        assert len(
            session.scalars(
                select(OutboxRow).where(OutboxRow.idempotency_key == key)
            ).all()
        ) == 1
