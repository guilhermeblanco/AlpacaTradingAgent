from __future__ import annotations

from statistics import mean
from typing import Iterable

from .models import EvaluationOutcome


def summarize_outcomes(outcomes: Iterable[EvaluationOutcome]) -> dict[str, float | int]:
    rows = list(outcomes)
    if not rows:
        return {"count": 0, "hit_rate_pct": 0.0, "mean_excess_return_pct": 0.0,
                "mean_asset_return_pct": 0.0, "total_estimated_cost_pct": 0.0}
    return {
        "count": len(rows),
        "hit_rate_pct": 100.0 * sum(row.directionally_correct for row in rows) / len(rows),
        "mean_excess_return_pct": mean(row.excess_return_pct for row in rows),
        "mean_asset_return_pct": mean(row.asset_return_pct for row in rows),
        "total_estimated_cost_pct": sum(row.estimated_cost_pct for row in rows),
    }
