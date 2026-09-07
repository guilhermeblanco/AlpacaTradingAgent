from __future__ import annotations

from datetime import datetime

from .models import EvaluationEpisode, EvaluationOutcome
from .point_in_time import validate_episode_point_in_time, validate_outcome_time


LONG_ACTIONS = {"BUY", "OPEN", "INCREASE", "LONG"}
SHORT_ACTIONS = {"SHORT"}


def calculate_outcome(
    episode: EvaluationEpisode,
    *,
    horizon: str,
    outcome_at: datetime,
    asset_price: float,
    benchmark_price: float,
    estimated_cost_pct: float = 0.0,
) -> EvaluationOutcome:
    validate_episode_point_in_time(episode)
    validate_outcome_time(episode, outcome_at)
    if asset_price <= 0 or benchmark_price <= 0:
        raise ValueError("Outcome prices must be positive")
    raw_asset_return = ((asset_price / episode.reference_price) - 1.0) * 100.0
    benchmark_return = ((benchmark_price / episode.benchmark_price) - 1.0) * 100.0
    action = episode.action.upper()
    signed_asset_return = -raw_asset_return if action in SHORT_ACTIONS else raw_asset_return
    net_asset_return = signed_asset_return - max(0.0, estimated_cost_pct)
    if action in LONG_ACTIONS:
        directionally_correct = raw_asset_return > 0
    elif action in SHORT_ACTIONS:
        directionally_correct = raw_asset_return < 0
    else:
        directionally_correct = abs(raw_asset_return) <= abs(benchmark_return)
    return EvaluationOutcome(
        decision_id=episode.decision_id,
        horizon=horizon,
        outcome_at=outcome_at,
        asset_price=asset_price,
        benchmark_price=benchmark_price,
        asset_return_pct=net_asset_return,
        benchmark_return_pct=benchmark_return,
        excess_return_pct=net_asset_return - benchmark_return,
        directionally_correct=directionally_correct,
        estimated_cost_pct=max(0.0, estimated_cost_pct),
    )
