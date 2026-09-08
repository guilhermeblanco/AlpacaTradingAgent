"""Durable reconciliation queue contracts."""

from __future__ import annotations

from pydantic import BaseModel


class ReconciliationTask(BaseModel):
    decision_id: str
    broker: str
    attempts: int
    execution_result: dict


class ReconciliationLeaseLost(RuntimeError):
    pass
