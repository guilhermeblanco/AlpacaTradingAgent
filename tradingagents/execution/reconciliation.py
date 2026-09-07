from __future__ import annotations

from enum import Enum
from typing import Optional, Protocol

from pydantic import BaseModel, Field

from .models import ExecutionPlan, PlanAction


class BrokerOrderStatus(str, Enum):
    NEW = "new"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class BrokerOrderSnapshot(BaseModel):
    order_id: str
    client_order_id: Optional[str] = None
    symbol: str
    side: str
    status: BrokerOrderStatus
    requested_quantity: Optional[float] = Field(default=None, ge=0)
    filled_quantity: float = Field(default=0, ge=0)
    filled_avg_price: Optional[float] = Field(default=None, gt=0)
    child_orders: list["BrokerOrderSnapshot"] = Field(default_factory=list)


class ReconciliationGateway(Protocol):
    def get_order_snapshot(self, *, order_id: Optional[str] = None,
                           client_order_id: Optional[str] = None) -> BrokerOrderSnapshot:
        ...


class LegReconciliation(BaseModel):
    leg_index: int
    order_id: Optional[str] = None
    status: BrokerOrderStatus
    expected_quantity: Optional[float] = None
    filled_quantity: float = 0
    protected_quantity: float = 0
    complete: bool
    problems: list[str] = Field(default_factory=list)


class ReconciliationReport(BaseModel):
    decision_id: str
    symbol: str
    complete: bool
    legs: list[LegReconciliation] = Field(default_factory=list)


class ExecutionReconciler:
    def __init__(self, gateway: ReconciliationGateway, *, quantity_tolerance: float = 1e-6):
        self.gateway = gateway
        self.quantity_tolerance = max(0.0, quantity_tolerance)

    def reconcile(self, plan: ExecutionPlan, actions: list[dict]) -> ReconciliationReport:
        reports: list[LegReconciliation] = []
        idempotency_keys = plan.metadata.get("leg_idempotency_keys", [])
        for index, leg in enumerate(plan.legs):
            if leg.action == PlanAction.HOLD:
                continue
            action = actions[index] if index < len(actions) else {}
            result = action.get("result", action)
            order_id = result.get("order_id")
            client_order_id = (
                idempotency_keys[index] if index < len(idempotency_keys) else result.get("client_order_id")
            )
            snapshot = self.gateway.get_order_snapshot(
                order_id=order_id, client_order_id=client_order_id
            )
            problems: list[str] = []
            terminal_success = snapshot.status == BrokerOrderStatus.FILLED
            terminal_failure = snapshot.status in {
                BrokerOrderStatus.CANCELED, BrokerOrderStatus.REJECTED, BrokerOrderStatus.EXPIRED,
            }
            if terminal_failure:
                problems.append(f"entry order is {snapshot.status.value}")
            expected = leg.quantity
            if expected is not None and terminal_success:
                if abs(snapshot.filled_quantity - expected) > self.quantity_tolerance:
                    problems.append("filled quantity does not match planned quantity")
            protected_quantity = sum(
                child.requested_quantity or 0.0
                for child in snapshot.child_orders
                if child.status not in {BrokerOrderStatus.CANCELED, BrokerOrderStatus.REJECTED, BrokerOrderStatus.EXPIRED}
            )
            expects_protection = bool(snapshot.child_orders)
            if expects_protection and snapshot.filled_quantity > 0:
                if abs(protected_quantity - snapshot.filled_quantity) > self.quantity_tolerance:
                    problems.append("protective child quantity does not match actual fill")
            reports.append(LegReconciliation(
                leg_index=index, order_id=snapshot.order_id, status=snapshot.status,
                expected_quantity=expected, filled_quantity=snapshot.filled_quantity,
                protected_quantity=protected_quantity,
                complete=terminal_success and not problems, problems=problems,
            ))
        return ReconciliationReport(
            decision_id=plan.decision_id, symbol=plan.symbol,
            complete=all(row.complete for row in reports), legs=reports,
        )
