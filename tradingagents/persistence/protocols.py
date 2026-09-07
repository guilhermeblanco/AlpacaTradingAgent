"""Ports implemented by local SQLite/JSONL and future PostgreSQL adapters."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from tradingagents.evaluation.models import EvaluationEpisode, EvaluationOutcome
    from tradingagents.lifecycle.models import LifecycleRecord, LifecycleStatus
    from tradingagents.operations.admission import AdmissionDecision


@runtime_checkable
class EventJournalPort(Protocol):
    def append(
        self,
        event_type: str,
        *,
        symbol: str,
        decision_id: str,
        run_id: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> str: ...


@runtime_checkable
class LifecycleRepositoryPort(Protocol):
    def get(self, decision_id: str) -> Optional[LifecycleRecord]: ...

    def create(
        self,
        *,
        decision_id: str,
        symbol: str,
        idempotency_key: str,
        valid_until: Optional[datetime] = None,
        run_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> LifecycleRecord: ...

    def transition(
        self,
        decision_id: str,
        status: LifecycleStatus,
        *,
        error: Optional[str] = None,
        result: Optional[dict[str, Any]] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> LifecycleRecord: ...

    def expire_due(self, now: Optional[datetime] = None) -> int: ...


@runtime_checkable
class EvaluationRepositoryPort(Protocol):
    def record_episode(self, episode: EvaluationEpisode) -> None: ...

    def get_episode(self, decision_id: str) -> Optional[EvaluationEpisode]: ...

    def record_outcome(self, outcome: EvaluationOutcome) -> None: ...

    def outcomes(
        self, *, experiment_id: Optional[str] = None
    ) -> list[EvaluationOutcome]: ...


@runtime_checkable
class AdmissionPolicyPort(Protocol):
    def try_admit(
        self,
        symbol: str,
        *,
        price: Optional[float] = None,
        estimated_tokens: int = 0,
        now: Optional[datetime] = None,
    ) -> AdmissionDecision: ...

    def complete(self, symbol: str) -> None: ...
