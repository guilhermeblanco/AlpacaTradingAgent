"""Durable broker order and cumulative-fill ledger contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol

from pydantic import BaseModel, Field

from .models import ExecutionResult
from .reconciliation import BrokerOrderSnapshot


class BrokerOrderRecord(BaseModel):
    order_key: str
    decision_id: str
    leg_index: int
    broker: str
    broker_order_id: Optional[str] = None
    client_order_id: str
    symbol: str
    side: str
    status: str
    requested_quantity: Optional[float] = None
    requested_notional: Optional[float] = None
    filled_quantity: float = Field(default=0, ge=0)
    filled_avg_price: Optional[float] = Field(default=None, gt=0)
    submitted_at: datetime
    updated_at: datetime
    terminal_at: Optional[datetime] = None


class OrderLedgerPort(Protocol):
    def record_submission(self, result: ExecutionResult) -> list[BrokerOrderRecord]: ...

    def apply_snapshot(
        self,
        *,
        decision_id: str,
        leg_index: int,
        snapshot: BrokerOrderSnapshot,
        observed_at: Optional[datetime] = None,
    ) -> BrokerOrderRecord: ...

    def orders_for_decision(self, decision_id: str) -> list[BrokerOrderRecord]: ...
