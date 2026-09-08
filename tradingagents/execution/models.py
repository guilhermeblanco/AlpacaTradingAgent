from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class PlanAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    CLOSE = "CLOSE"
    HOLD = "HOLD"


class ExecutionLeg(BaseModel):
    action: PlanAction
    side: Optional[str] = None
    notional_usd: float = Field(default=0.0, ge=0)
    quantity: Optional[float] = Field(default=None, ge=0)
    risk_reducing: bool = False
    reason: str


class ExecutionPlan(BaseModel):
    decision_id: str
    symbol: str
    intent_schema_version: str
    current_allocation_pct: float
    target_allocation_pct: Optional[float] = None
    current_notional_usd: float
    target_notional_usd: Optional[float] = None
    delta_notional_usd: float
    reference_price: Optional[float] = None
    legs: list[ExecutionLeg] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_noop(self) -> bool:
        return not self.legs or all(leg.action == PlanAction.HOLD for leg in self.legs)


class ExecutionResult(BaseModel):
    success: bool
    decision_id: str
    symbol: str
    gateway: str
    plan: ExecutionPlan
    actions: list[dict[str, Any]] = Field(default_factory=list)
    validations: list[dict[str, Any]] = Field(default_factory=list)
    error: Optional[str] = None
    safety_blocked: bool = False
    submission_uncertain: bool = False
    journal_path: Optional[str] = None
