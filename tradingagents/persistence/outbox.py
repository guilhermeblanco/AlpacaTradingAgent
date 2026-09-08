"""Transactional outbox contracts and a bounded at-least-once dispatcher."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional, Protocol

from pydantic import BaseModel, Field


class OutboxLeaseLost(RuntimeError):
    pass


class OutboxMessage(BaseModel):
    outbox_id: str
    topic: str
    idempotency_key: str
    payload: dict[str, Any]
    created_at: datetime
    available_at: datetime
    locked_until: Optional[datetime] = None
    locked_by: Optional[str] = None
    attempts: int = Field(ge=0)


class OutboxPort(Protocol):
    def enqueue(
        self,
        topic: str,
        *,
        idempotency_key: str,
        payload: dict[str, Any],
        available_at: Optional[datetime] = None,
    ) -> str: ...

    def claim(
        self,
        *,
        worker_id: str,
        limit: int = 10,
        lease_seconds: int = 60,
        now: Optional[datetime] = None,
    ) -> list[OutboxMessage]: ...

    def mark_processed(
        self,
        outbox_id: str,
        *,
        worker_id: str,
        processed_at: Optional[datetime] = None,
    ) -> None: ...

    def mark_failed(
        self,
        outbox_id: str,
        *,
        worker_id: str,
        error: str,
        retry_at: datetime,
        dead_letter: bool = False,
    ) -> None: ...


class OutboxUnitOfWorkFactory(Protocol):
    def __call__(self): ...


class OutboxDispatchResult(BaseModel):
    claimed: int = 0
    processed: int = 0
    failed: int = 0
    dead_lettered: int = 0


class OutboxDispatcher:
    """Dispatches claimed work outside database transactions.

    Handlers must pass ``message.idempotency_key`` to the remote system. A
    process can fail after the remote call succeeds but before acknowledgement,
    so duplicate delivery remains possible by design.
    """

    def __init__(
        self,
        unit_of_work_factory: OutboxUnitOfWorkFactory,
        handlers: Mapping[str, Callable[[OutboxMessage], None]],
        *,
        worker_id: str,
        lease_seconds: int = 60,
        max_attempts: int = 5,
        base_retry_seconds: int = 5,
        max_retry_seconds: int = 900,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        self.unit_of_work_factory = unit_of_work_factory
        self.handlers = dict(handlers)
        self.worker_id = worker_id
        self.lease_seconds = max(1, int(lease_seconds))
        self.max_attempts = max(1, int(max_attempts))
        self.base_retry_seconds = max(1, int(base_retry_seconds))
        self.max_retry_seconds = max(
            self.base_retry_seconds, int(max_retry_seconds)
        )
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def dispatch_once(self, *, limit: int = 10) -> OutboxDispatchResult:
        now = self.clock()
        with self.unit_of_work_factory() as uow:
            messages = uow.outbox.claim(
                worker_id=self.worker_id,
                limit=limit,
                lease_seconds=self.lease_seconds,
                now=now,
            )
            uow.commit()

        result = OutboxDispatchResult(claimed=len(messages))
        for message in messages:
            try:
                handler = self.handlers[message.topic]
                handler(message)
            except Exception as exc:
                dead_letter = message.attempts >= self.max_attempts
                retry_at = self.clock() + timedelta(
                    seconds=self._retry_delay(message.attempts)
                )
                with self.unit_of_work_factory() as uow:
                    uow.outbox.mark_failed(
                        message.outbox_id,
                        worker_id=self.worker_id,
                        error=f"{type(exc).__name__}: {exc}",
                        retry_at=retry_at,
                        dead_letter=dead_letter,
                    )
                    uow.commit()
                result.failed += 1
                result.dead_lettered += int(dead_letter)
                continue

            with self.unit_of_work_factory() as uow:
                uow.outbox.mark_processed(
                    message.outbox_id,
                    worker_id=self.worker_id,
                    processed_at=self.clock(),
                )
                uow.commit()
            result.processed += 1
        return result

    def _retry_delay(self, attempts: int) -> int:
        return min(
            self.max_retry_seconds,
            self.base_retry_seconds * (2 ** max(0, attempts - 1)),
        )
