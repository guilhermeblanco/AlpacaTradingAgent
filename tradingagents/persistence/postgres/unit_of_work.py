"""A single SQLAlchemy transaction spanning all PostgreSQL persistence ports."""

from __future__ import annotations

from types import TracebackType
from typing import Optional, Type

from sqlalchemy.orm import Session, sessionmaker

from .repositories import (
    PostgresAdmissionPolicy,
    PostgresAccountSnapshotRepository,
    PostgresDecisionExplorerRepository,
    PostgresEvaluationRepository,
    PostgresEventJournal,
    PostgresLifecycleRepository,
    PostgresOutboxRepository,
    PostgresOrderLedger,
    PostgresOperationalRepository,
    PostgresPortfolioReservationRepository,
    PostgresReconciliationQueue,
    PostgresWorkbenchRepository,
)


class PostgresUnitOfWork:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        admission_options: Optional[dict] = None,
    ):
        self.session_factory = session_factory
        self.admission_options = admission_options or {}
        self.session: Optional[Session] = None

    def __enter__(self) -> "PostgresUnitOfWork":
        self.session = self.session_factory()
        self.lifecycle = PostgresLifecycleRepository(self.session)
        self.evaluation = PostgresEvaluationRepository(self.session)
        self.admission = PostgresAdmissionPolicy(
            self.session, **self.admission_options
        )
        self.journal = PostgresEventJournal(self.session)
        self.outbox = PostgresOutboxRepository(self.session)
        self.orders = PostgresOrderLedger(self.session)
        self.portfolio_reservations = PostgresPortfolioReservationRepository(
            self.session
        )
        self.reconciliation_queue = PostgresReconciliationQueue(self.session)
        self.operations = PostgresOperationalRepository(self.session)
        self.account_snapshots = PostgresAccountSnapshotRepository(self.session)
        self.decision_explorer = PostgresDecisionExplorerRepository(self.session)
        self.workbench = PostgresWorkbenchRepository(self.session)
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        if self.session is None:
            return
        try:
            if exc_type is not None:
                self.rollback()
        finally:
            self.session.close()
            self.session = None

    def commit(self) -> None:
        if self.session is None:
            raise RuntimeError("Unit of work is not active")
        self.session.commit()

    def rollback(self) -> None:
        if self.session is not None:
            self.session.rollback()
