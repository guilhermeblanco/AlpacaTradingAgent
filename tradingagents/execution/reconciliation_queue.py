"""Durable reconciliation queue contracts."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ReconciliationTask(BaseModel):
    decision_id: str
    broker: str
    attempts: int
    execution_result: dict
    last_error: Optional[str] = None


class ReconciliationLeaseLost(RuntimeError):
    pass
