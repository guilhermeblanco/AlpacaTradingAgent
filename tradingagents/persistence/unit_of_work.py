"""Transaction boundary used by persistence-aware application services."""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import Optional, Protocol, Type

from typing_extensions import Self

from .protocols import (
    AdmissionPolicyPort,
    EvaluationRepositoryPort,
    EventJournalPort,
    LifecycleRepositoryPort,
)


class UnitOfWork(Protocol):
    lifecycle: LifecycleRepositoryPort
    evaluation: EvaluationRepositoryPort
    admission: AdmissionPolicyPort
    journal: EventJournalPort

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


@dataclass
class PassthroughUnitOfWork:
    """Compatibility boundary for independently committed legacy adapters.

    It intentionally provides no cross-store atomicity. PostgreSQL replaces
    this adapter with a real session-backed transaction in the next increment.
    """

    lifecycle: LifecycleRepositoryPort
    evaluation: EvaluationRepositoryPort
    admission: AdmissionPolicyPort
    journal: EventJournalPort
    committed: bool = False
    rolled_back: bool = False

    def __enter__(self) -> "PassthroughUnitOfWork":
        self.committed = False
        self.rolled_back = False
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        if exc_type is not None:
            self.rollback()

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True
