from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class LifecycleStatus(str, Enum):
    RECEIVED = "received"
    VALIDATED = "validated"
    PLANNED = "planned"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = {
    LifecycleStatus.SUCCEEDED,
    LifecycleStatus.FILLED,
    LifecycleStatus.BLOCKED,
    LifecycleStatus.FAILED,
    LifecycleStatus.EXPIRED,
    LifecycleStatus.CANCELLED,
}


class LifecycleRecord(BaseModel):
    decision_id: str
    symbol: str
    status: LifecycleStatus
    idempotency_key: str
    created_at: datetime
    updated_at: datetime
    valid_until: Optional[datetime] = None
    run_id: Optional[str] = None
    error: Optional[str] = None
    result: Optional[dict[str, Any]] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES
