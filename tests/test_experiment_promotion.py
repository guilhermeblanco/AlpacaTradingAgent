from datetime import datetime, timezone

import pytest

from tradingagents.evaluation import (
    EvaluationOutcome,
    PromotionPolicy,
    PromotionStatus,
    assess_promotion,
)


def _outcome(value, *, horizon="5d", correct=True, cost=0.1, index=0):
    return EvaluationOutcome(
        decision_id=f"decision-{horizon}-{index}-{value}",
        horizon=horizon,
        outcome_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        asset_price=100,
        benchmark_price=100,
        asset_return_pct=value,
        benchmark_return_pct=0,
        excess_return_pct=value,
        directionally_correct=correct,
        estimated_cost_pct=cost,
    )


def _series(values, **kwargs):
    return [_outcome(value, index=index, **kwargs) for index, value in enumerate(values)]


def test_promotion_requires_enough_champion_and_challenger_outcomes() -> None:
    decision = assess_promotion(
        _series([2.0] * 5),
        _series([0.2] * 30),
        horizon="5d",
    )

    assert decision.status is PromotionStatus.INSUFFICIENT_DATA
    assert "5 outcomes" in decision.reasons[0]


def test_promotion_rejects_positive_but_statistically_unreliable_mean() -> None:
    noisy = _series([10.0, -8.0] * 15)
    champion = _series([0.0] * 30)

    decision = assess_promotion(noisy, champion, horizon="5d")

    assert decision.challenger.mean_excess_return_pct == 1.0
    assert decision.challenger.excess_return_lcb_pct < 0
    assert decision.status is PromotionStatus.REJECTED


def test_stable_improvement_is_eligible() -> None:
    challenger = _series([2.0] * 30)
    champion = _series([0.2] * 30)

    decision = assess_promotion(challenger, champion, horizon="5d")

    assert decision.status is PromotionStatus.ELIGIBLE
    assert decision.uplift_lcb_pct == pytest.approx(1.8)


def test_cost_and_horizon_are_hard_gates() -> None:
    costly = _series([2.0] * 30, cost=1.0)
    champion = _series([0.2] * 30)
    policy = PromotionPolicy(max_mean_cost_pct=0.5)

    decision = assess_promotion(costly, champion, horizon="5d", policy=policy)
    assert decision.status is PromotionStatus.REJECTED
    assert "cost exceeds" in decision.reasons[-1]

    with pytest.raises(ValueError, match="exactly one"):
        assess_promotion(
            [_outcome(2.0, horizon="1d")], champion, horizon="5d", policy=policy
        )
