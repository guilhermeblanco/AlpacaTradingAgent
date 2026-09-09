"""Read models for decision-to-outcome lifecycle exploration."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class DecisionActivitySummary(BaseModel):
    decision_id: str
    symbol: str
    status: str
    created_at: datetime
    updated_at: datetime
    broker: Optional[str] = None
    order_count: int = 0
    filled_quantity: float = 0.0
    error: Optional[str] = None


class DecisionTimelineEvent(BaseModel):
    occurred_at: datetime
    category: str
    label: str
    status: Optional[str] = None
    details: dict[str, Any] = Field(default_factory=dict)


class DecisionActivityDetail(BaseModel):
    summary: DecisionActivitySummary
    timeline: list[DecisionTimelineEvent] = Field(default_factory=list)
