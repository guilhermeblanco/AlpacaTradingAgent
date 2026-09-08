"""Deterministic gates for promoting a evaluated strategy experiment."""

from __future__ import annotations

import math
from enum import Enum
from statistics import mean, stdev

from pydantic import BaseModel, Field

from .models import EvaluationOutcome


class PromotionStatus(str, Enum):
    INSUFFICIENT_DATA = "insufficient_data"
    REJECTED = "rejected"
    ELIGIBLE = "eligible"


class PromotionPolicy(BaseModel):
    min_outcomes: int = Field(default=30, ge=2)
    min_hit_rate_pct: float = Field(default=50.0, ge=0, le=100)
    min_mean_excess_return_pct: float = 0.0
    min_excess_return_lcb_pct: float = 0.0
    min_uplift_lcb_pct: float = 0.0
    max_mean_cost_pct: float = Field(default=0.5, ge=0)
    confidence_z: float = Field(default=1.645, gt=0)


class ExperimentScorecard(BaseModel):
    count: int
    hit_rate_pct: float
    mean_asset_return_pct: float
    mean_excess_return_pct: float
    mean_cost_pct: float
    excess_return_stddev_pct: float
    excess_return_lcb_pct: float


class PromotionDecision(BaseModel):
    status: PromotionStatus
    horizon: str
    challenger: ExperimentScorecard
    champion: ExperimentScorecard
    uplift_pct: float
    uplift_lcb_pct: float
    reasons: list[str]


def _scorecard(
    outcomes: list[EvaluationOutcome], *, confidence_z: float
) -> ExperimentScorecard:
    excess = [row.excess_return_pct for row in outcomes]
    count = len(outcomes)
    excess_mean = mean(excess) if excess else 0.0
    excess_stddev = stdev(excess) if count > 1 else 0.0
    standard_error = excess_stddev / math.sqrt(count) if count else 0.0
    return ExperimentScorecard(
        count=count,
        hit_rate_pct=(
            100.0 * sum(row.directionally_correct for row in outcomes) / count
            if count
            else 0.0
        ),
        mean_asset_return_pct=(
            mean(row.asset_return_pct for row in outcomes) if count else 0.0
        ),
        mean_excess_return_pct=excess_mean,
        mean_cost_pct=(
            mean(row.estimated_cost_pct for row in outcomes) if count else 0.0
        ),
        excess_return_stddev_pct=excess_stddev,
        excess_return_lcb_pct=excess_mean - confidence_z * standard_error,
    )


def assess_promotion(
    challenger_outcomes: list[EvaluationOutcome],
    champion_outcomes: list[EvaluationOutcome],
    *,
    horizon: str,
    policy: PromotionPolicy | None = None,
) -> PromotionDecision:
    policy = policy or PromotionPolicy()
    if not horizon.strip():
        raise ValueError("promotion horizon is required")
    for row in [*challenger_outcomes, *champion_outcomes]:
        if row.horizon != horizon:
            raise ValueError("promotion inputs must use exactly one requested horizon")
    challenger = _scorecard(challenger_outcomes, confidence_z=policy.confidence_z)
    champion = _scorecard(champion_outcomes, confidence_z=policy.confidence_z)
    uplift = challenger.mean_excess_return_pct - champion.mean_excess_return_pct
    uplift_standard_error = math.sqrt(
        (
            challenger.excess_return_stddev_pct**2 / challenger.count
            if challenger.count
            else 0.0
        )
        + (
            champion.excess_return_stddev_pct**2 / champion.count
            if champion.count
            else 0.0
        )
    )
    uplift_lcb = uplift - policy.confidence_z * uplift_standard_error
    reasons = []
    if challenger.count < policy.min_outcomes:
        reasons.append(
            f"Challenger has {challenger.count} outcomes; {policy.min_outcomes} required."
        )
    if champion.count < policy.min_outcomes:
        reasons.append(
            f"Champion has {champion.count} outcomes; {policy.min_outcomes} required."
        )
    insufficient = bool(reasons)
    if not insufficient:
        checks = [
            (
                challenger.hit_rate_pct >= policy.min_hit_rate_pct,
                "Challenger hit rate is below policy.",
            ),
            (
                challenger.mean_excess_return_pct
                >= policy.min_mean_excess_return_pct,
                "Challenger mean excess return is below policy.",
            ),
            (
                challenger.excess_return_lcb_pct
                >= policy.min_excess_return_lcb_pct,
                "Challenger excess-return lower bound is below policy.",
            ),
            (
                uplift_lcb >= policy.min_uplift_lcb_pct,
                "Challenger uplift lower bound is below policy.",
            ),
            (
                challenger.mean_cost_pct <= policy.max_mean_cost_pct,
                "Challenger mean estimated cost exceeds policy.",
            ),
        ]
        reasons.extend(reason for passed, reason in checks if not passed)
    if insufficient:
        status = PromotionStatus.INSUFFICIENT_DATA
    elif reasons:
        status = PromotionStatus.REJECTED
    else:
        status = PromotionStatus.ELIGIBLE
        reasons.append("All deterministic promotion gates passed.")
    return PromotionDecision(
        status=status,
        horizon=horizon,
        challenger=challenger,
        champion=champion,
        uplift_pct=uplift,
        uplift_lcb_pct=uplift_lcb,
        reasons=reasons,
    )
