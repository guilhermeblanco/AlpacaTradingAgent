"""Storage contracts shared by domain services and infrastructure adapters."""

from .events import EventEnvelope, payload_hash
from .protocols import (
    AdmissionPolicyPort,
    EvaluationRepositoryPort,
    EventJournalPort,
    LifecycleRepositoryPort,
)
from .outbox import (
    OutboxDispatchResult,
    OutboxDispatcher,
    OutboxLeaseLost,
    OutboxMessage,
    OutboxPort,
)
from .unit_of_work import OutboxUnitOfWork, PassthroughUnitOfWork, UnitOfWork
from .runtime import PersistenceRuntime, build_persistence_runtime

__all__ = [
    "AdmissionPolicyPort",
    "EvaluationRepositoryPort",
    "EventEnvelope",
    "EventJournalPort",
    "LifecycleRepositoryPort",
    "PassthroughUnitOfWork",
    "OutboxDispatchResult",
    "OutboxDispatcher",
    "OutboxLeaseLost",
    "OutboxMessage",
    "OutboxPort",
    "OutboxUnitOfWork",
    "PersistenceRuntime",
    "UnitOfWork",
    "payload_hash",
    "build_persistence_runtime",
]
