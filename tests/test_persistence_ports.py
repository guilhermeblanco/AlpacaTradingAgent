import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from tradingagents.evaluation import EvaluationRepository
from tradingagents.execution.journal import ExecutionJournal
from tradingagents.lifecycle import LifecycleRepository
from tradingagents.operations import AnalysisAdmissionPolicy
from tradingagents.persistence import (
    AdmissionPolicyPort,
    EvaluationRepositoryPort,
    EventEnvelope,
    EventJournalPort,
    LifecycleRepositoryPort,
    PassthroughUnitOfWork,
    payload_hash,
)


def test_legacy_adapters_satisfy_persistence_ports():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        lifecycle = LifecycleRepository(root / "lifecycle.sqlite3")
        evaluation = EvaluationRepository(root / "evaluation.sqlite3")
        admission = AnalysisAdmissionPolicy(root / "admission.sqlite3")
        journal = ExecutionJournal(root)

        assert isinstance(lifecycle, LifecycleRepositoryPort)
        assert isinstance(evaluation, EvaluationRepositoryPort)
        assert isinstance(admission, AdmissionPolicyPort)
        assert isinstance(journal, EventJournalPort)


def test_event_envelope_hashes_canonical_payload_and_rejects_tampering():
    first = EventEnvelope(
        event_type="intent.received",
        aggregate_type="decision",
        aggregate_id="decision-1",
        payload={"symbol": "AAPL", "amount": 1000},
    )
    second_hash = payload_hash({"amount": 1000, "symbol": "AAPL"})

    assert first.payload_sha256 == second_hash

    with pytest.raises(ValueError, match="does not match"):
        EventEnvelope(
            event_type="intent.received",
            aggregate_type="decision",
            aggregate_id="decision-1",
            payload={"symbol": "AAPL"},
            payload_sha256="incorrect",
        )


def test_event_envelope_requires_timezone_aware_timestamps():
    with pytest.raises(ValueError, match="timezone"):
        EventEnvelope(
            event_type="intent.received",
            aggregate_type="decision",
            aggregate_id="decision-1",
            occurred_at=datetime(2026, 1, 1),
        )


def test_passthrough_unit_of_work_marks_commit_and_rollback():
    marker = object()
    uow = PassthroughUnitOfWork(marker, marker, marker, marker)

    with uow:
        uow.commit()
    assert uow.committed
    assert not uow.rolled_back

    with pytest.raises(RuntimeError):
        with uow:
            raise RuntimeError("fail")
    assert uow.rolled_back
