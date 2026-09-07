from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class EvaluationEpisode(BaseModel):
    decision_id: str
    symbol: str
    action: str
    decision_at: datetime
    data_as_of: datetime
    reference_price: float = Field(gt=0)
    benchmark_symbol: str = "SPY"
    benchmark_price: float = Field(gt=0)
    confidence: Optional[float] = None
    experiment_id: str = "default"
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvaluationOutcome(BaseModel):
    decision_id: str
    horizon: str
    outcome_at: datetime
    asset_price: float = Field(gt=0)
    benchmark_price: float = Field(gt=0)
    asset_return_pct: float
    benchmark_return_pct: float
    excess_return_pct: float
    directionally_correct: bool
    estimated_cost_pct: float = Field(default=0.0, ge=0)
