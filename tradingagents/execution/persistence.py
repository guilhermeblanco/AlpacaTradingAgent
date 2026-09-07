"""Atomic lifecycle and event recording for execution stages."""

from __future__ import annotations

from typing import Any, Callable, Optional

from tradingagents.lifecycle import LifecycleService, LifecycleStatus
from tradingagents.lifecycle.models import LifecycleRecord
from tradingagents.persistence.protocols import EventJournalPort


class ExecutionPersistence:
    def __init__(
        self,
        journal: EventJournalPort,
        *,
        lifecycle: Optional[LifecycleService] = None,
        unit_of_work_factory: Optional[Callable[[], Any]] = None,
        lifecycle_enabled: bool = True,
        lifecycle_ttl_seconds: int = 900,
    ):
        self.journal = journal
        self.lifecycle = lifecycle
        self.unit_of_work_factory = unit_of_work_factory
        self.lifecycle_enabled = lifecycle_enabled
        self.lifecycle_ttl_seconds = lifecycle_ttl_seconds

    def begin(
        self,
        *,
        decision_id: str,
        symbol: str,
        run_id: Optional[str],
        payload: dict[str, Any],
    ) -> tuple[Optional[LifecycleRecord], str]:
        if self.unit_of_work_factory is None:
            record = (
                self.lifecycle.begin(
                    decision_id=decision_id, symbol=symbol, run_id=run_id
                )
                if self.lifecycle is not None
                else None
            )
            reference = self.journal.append(
                "intent_received",
                symbol=symbol,
                decision_id=decision_id,
                run_id=run_id,
                payload=payload,
            )
            return record, reference

        with self.unit_of_work_factory() as uow:
            record = None
            if self.lifecycle_enabled:
                record = LifecycleService(
                    uow.lifecycle,
                    default_ttl_seconds=self.lifecycle_ttl_seconds,
                ).begin(decision_id=decision_id, symbol=symbol, run_id=run_id)
            reference = uow.journal.append(
                "intent_received",
                symbol=symbol,
                decision_id=decision_id,
                run_id=run_id,
                payload=payload,
            )
            uow.commit()
        return record, reference

    def record(
        self,
        event_type: str,
        *,
        decision_id: str,
        symbol: str,
        run_id: Optional[str],
        payload: Optional[dict[str, Any]] = None,
        status: Optional[LifecycleStatus] = None,
        error: Optional[str] = None,
        result: Optional[dict[str, Any]] = None,
    ) -> str:
        transition_args = {}
        if error is not None:
            transition_args["error"] = error
        if result is not None:
            transition_args["result"] = result
        if payload is not None:
            transition_args["payload"] = payload

        if self.unit_of_work_factory is None:
            if status is not None and self.lifecycle is not None:
                self.lifecycle.transition(decision_id, status, **transition_args)
            return self.journal.append(
                event_type,
                symbol=symbol,
                decision_id=decision_id,
                run_id=run_id,
                payload=payload,
            )

        with self.unit_of_work_factory() as uow:
            if status is not None and self.lifecycle_enabled:
                uow.lifecycle.transition(decision_id, status, **transition_args)
            reference = uow.journal.append(
                event_type,
                symbol=symbol,
                decision_id=decision_id,
                run_id=run_id,
                payload=payload,
            )
            uow.commit()
        return reference
