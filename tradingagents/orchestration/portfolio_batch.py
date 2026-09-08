"""Analysis-to-allocation orchestration using one portfolio snapshot."""

from __future__ import annotations

from typing import Any, Callable

import pandas as pd
from pydantic import BaseModel

from tradingagents.agents.schemas import TradeIntent
from tradingagents.broker.snapshot import SnapshotProvider
from tradingagents.portfolio import PortfolioLimitsConfig
from tradingagents.portfolio.batch import (
    PortfolioDecisionBatch,
    PortfolioIntentRequest,
    allocate_intent_batch,
)

from .batch import BatchOrchestrator, BatchResult, Candidate


class PortfolioBatchRun(BaseModel):
    analysis_results: list[BatchResult]
    decision_batch: PortfolioDecisionBatch


def run_portfolio_decision_batch(
    orchestrator: BatchOrchestrator,
    candidates: list[Candidate],
    analysis_handler: Callable[[Candidate], Any],
    *,
    provider: str,
    snapshot_provider: SnapshotProvider,
    price_history: dict[str, pd.DataFrame],
    requested_notional: Callable[[Candidate, TradeIntent], float],
    limits: PortfolioLimitsConfig,
    max_symbol_concentration_pct: float = 25.0,
) -> PortfolioBatchRun:
    analysis_results = orchestrator.run(
        candidates, analysis_handler, provider=provider
    )
    requests = []
    for result in analysis_results:
        if not result.success or result.cancelled:
            continue
        value = result.value
        if isinstance(value, dict) and ("trade_intent" in value or "intent" in value):
            value = value.get("trade_intent", value.get("intent"))
        intent = value if isinstance(value, TradeIntent) else TradeIntent.model_validate(value)
        requests.append(
            PortfolioIntentRequest(
                intent=intent,
                requested_notional_usd=requested_notional(result.candidate, intent),
                candidate_score=result.candidate.score,
            )
        )

    snapshot = snapshot_provider.get_portfolio_snapshot()
    return PortfolioBatchRun(
        analysis_results=analysis_results,
        decision_batch=allocate_intent_batch(
            requests,
            snapshot,
            price_history,
            limits=limits,
            max_symbol_concentration_pct=max_symbol_concentration_pct,
        ),
    )
