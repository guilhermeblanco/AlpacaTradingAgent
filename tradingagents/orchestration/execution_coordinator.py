"""Durably reserve and dispatch portfolio allocations one decision at a time."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from tradingagents.agents.schemas import TradeIntent
from tradingagents.execution.models import ExecutionResult
from tradingagents.portfolio.batch import BatchAllocationStatus, PortfolioDecisionBatch


class AllocationDispatchStatus(str, Enum):
    ACCEPTED = "accepted"
    RELEASED = "released"
    SKIPPED = "skipped"


class AllocationDispatchResult(BaseModel):
    decision_id: str
    symbol: str
    status: AllocationDispatchStatus
    approved_notional_usd: float
    execution_result: Optional[dict[str, Any]] = None
    error: Optional[str] = None


class PortfolioDispatchResult(BaseModel):
    reservation_id: str
    batch_id: str
    allocations: list[AllocationDispatchResult] = Field(default_factory=list)


def _as_result_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, ExecutionResult):
        return result.model_dump(mode="json")
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json")
    if isinstance(result, dict):
        return result
    raise TypeError("executor must return a mapping or Pydantic model")


def _accepted_by_broker(result: dict[str, Any]) -> bool:
    if result.get("submission_uncertain"):
        return True
    if not result.get("success"):
        return False
    for action in result.get("actions") or []:
        if not isinstance(action, dict):
            continue
        remote = action.get("result", action)
        if isinstance(remote, dict) and remote.get("order_id"):
            return True
    return False


class ReservationAwareExecutionCoordinator:
    """Coordinates DB claims around broker calls without holding a transaction."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        executor: Callable[..., Any],
        *,
        worker_id: Optional[str] = None,
        claim_lease_seconds: int = 60,
    ):
        self.unit_of_work_factory = unit_of_work_factory
        self.executor = executor
        self.worker_id = worker_id or f"execution-coordinator-{uuid4()}"
        self.claim_lease_seconds = max(1, int(claim_lease_seconds))

    def execute(
        self,
        batch: PortfolioDecisionBatch,
        intents: Mapping[str, TradeIntent],
        *,
        account_key: str,
        reservation_ttl_seconds: int = 300,
        run_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> PortfolioDispatchResult:
        now = now or datetime.now(timezone.utc)
        with self.unit_of_work_factory() as uow:
            reservation = uow.portfolio_reservations.reserve(
                batch,
                account_key=account_key,
                ttl_seconds=reservation_ttl_seconds,
                now=now,
            )
            uow.commit()

        dispatch = PortfolioDispatchResult(
            reservation_id=reservation.reservation_id,
            batch_id=batch.batch_id,
        )
        allocations = sorted(
            (
                allocation
                for allocation in reservation.batch.allocations
                if allocation.status == BatchAllocationStatus.APPROVED
                and allocation.approved_notional_usd > 0
            ),
            key=lambda allocation: (
                allocation.priority,
                allocation.symbol,
                allocation.decision_id,
            ),
        )
        for allocation in allocations:
            with self.unit_of_work_factory() as uow:
                claimed = uow.portfolio_reservations.claim_allocation(
                    reservation.reservation_id,
                    decision_id=allocation.decision_id,
                    worker_id=self.worker_id,
                    lease_seconds=self.claim_lease_seconds,
                    now=now,
                )
                uow.commit()
            if not claimed:
                dispatch.allocations.append(
                    AllocationDispatchResult(
                        decision_id=allocation.decision_id,
                        symbol=allocation.symbol,
                        status=AllocationDispatchStatus.SKIPPED,
                        approved_notional_usd=allocation.approved_notional_usd,
                    )
                )
                continue

            result_dict = None
            error = None
            try:
                intent = intents[allocation.decision_id]
                result_dict = _as_result_dict(
                    self.executor(
                        allocation.symbol,
                        intent,
                        allocation.approved_notional_usd,
                        run_id=run_id,
                    )
                )
                accepted = _accepted_by_broker(result_dict)
                if not accepted:
                    error = result_dict.get("error") or "broker did not accept an order"
            except Exception as exc:
                accepted = False
                error = str(exc)

            with self.unit_of_work_factory() as uow:
                if accepted:
                    uow.portfolio_reservations.consume(
                        reservation.reservation_id,
                        decision_id=allocation.decision_id,
                        worker_id=self.worker_id,
                        now=now,
                    )
                    status = AllocationDispatchStatus.ACCEPTED
                else:
                    uow.portfolio_reservations.release_allocation(
                        reservation.reservation_id,
                        decision_id=allocation.decision_id,
                        worker_id=self.worker_id,
                        now=now,
                    )
                    status = AllocationDispatchStatus.RELEASED
                uow.commit()
            dispatch.allocations.append(
                AllocationDispatchResult(
                    decision_id=allocation.decision_id,
                    symbol=allocation.symbol,
                    status=status,
                    approved_notional_usd=allocation.approved_notional_usd,
                    execution_result=result_dict,
                    error=error,
                )
            )
        return dispatch
