"""Deterministic allocation of simultaneous intents against one portfolio."""

from __future__ import annotations

from enum import Enum
from datetime import datetime, timezone
from typing import Dict, Optional
from uuid import uuid4

import pandas as pd
from pydantic import BaseModel, Field

from tradingagents.agents.schemas import IntentType, TradeIntent
from tradingagents.broker.models import PortfolioSnapshot
from tradingagents.execution.journal import snapshot_hash

from . import PortfolioLimitsConfig, assess_new_position


class BatchAllocationStatus(str, Enum):
    APPROVED = "approved"
    BLOCKED = "blocked"
    RISK_REDUCING = "risk_reducing"
    NOOP = "noop"


class PortfolioIntentRequest(BaseModel):
    intent: TradeIntent
    requested_notional_usd: float = Field(ge=0)
    candidate_score: float = 0.0


class PortfolioAllocation(BaseModel):
    decision_id: str
    symbol: str
    status: BatchAllocationStatus
    requested_notional_usd: float
    approved_notional_usd: float = Field(ge=0)
    priority: int
    reasons: list[str] = Field(default_factory=list)


class PortfolioDecisionBatch(BaseModel):
    batch_id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    snapshot_hash: str
    snapshot_captured_at: str
    account_equity_usd: float
    starting_gross_exposure_usd: float
    starting_symbol_exposure_usd: dict[str, float] = Field(default_factory=dict)
    gross_limit_usd: Optional[float] = None
    max_symbol_concentration_pct: Optional[float] = None
    ending_reserved_exposure_usd: float
    allocations: list[PortfolioAllocation]


def _symbol_key(symbol: str) -> str:
    return (symbol or "").upper().replace("/", "").strip()


def allocate_intent_batch(
    requests: list[PortfolioIntentRequest],
    snapshot: PortfolioSnapshot,
    price_history: Dict[str, pd.DataFrame],
    *,
    limits: PortfolioLimitsConfig,
    max_symbol_concentration_pct: float = 25.0,
) -> PortfolioDecisionBatch:
    decision_ids = [row.intent.decision_id for row in requests]
    if len(decision_ids) != len(set(decision_ids)):
        raise ValueError("decision IDs must be unique within a portfolio batch")
    equity = snapshot.account.equity
    open_positions: dict[str, float] = {}
    for position in snapshot.positions:
        key = _symbol_key(position.symbol)
        open_positions[key] = open_positions.get(key, 0.0) + abs(
            position.market_value
        )
    canonical_history = {
        _symbol_key(symbol): frame for symbol, frame in price_history.items()
    }
    starting_gross = sum(open_positions.values())
    starting_symbol_exposure = dict(open_positions)
    configured_gross_limit = (
        equity * limits.max_gross_exposure_pct / 100.0
        if limits.enabled and limits.max_gross_exposure_pct > 0
        else None
    )
    gross_limit = configured_gross_limit if configured_gross_limit is not None else float("inf")
    reserved = starting_gross
    allocations = []

    reducing = {IntentType.REDUCE, IntentType.CLOSE}
    additions = []
    for request in requests:
        if request.intent.intent_type in reducing:
            allocations.append(
                PortfolioAllocation(
                    decision_id=request.intent.decision_id,
                    symbol=request.intent.symbol,
                    status=BatchAllocationStatus.RISK_REDUCING,
                    requested_notional_usd=request.requested_notional_usd,
                    approved_notional_usd=request.requested_notional_usd,
                    priority=0,
                    reasons=[
                        "Risk-reducing intent does not consume exposure headroom."
                    ],
                )
            )
        elif request.intent.intent_type == IntentType.HOLD:
            allocations.append(
                PortfolioAllocation(
                    decision_id=request.intent.decision_id,
                    symbol=request.intent.symbol,
                    status=BatchAllocationStatus.NOOP,
                    requested_notional_usd=request.requested_notional_usd,
                    approved_notional_usd=0,
                    priority=0,
                    reasons=["Hold intent."],
                )
            )
        else:
            additions.append(request)

    additions.sort(
        key=lambda row: (
            -(row.intent.confidence_score or 0),
            -row.candidate_score,
            row.intent.symbol,
            row.intent.decision_id,
        )
    )
    for priority, request in enumerate(additions, start=1):
        intent = request.intent
        symbol_key = _symbol_key(intent.symbol)
        requested = request.requested_notional_usd
        if intent.max_notional_usd is not None:
            requested = min(requested, intent.max_notional_usd)
        current = open_positions.get(symbol_key, 0.0)
        if intent.target_portfolio_pct is not None:
            target_notional = equity * intent.target_portfolio_pct / 100.0
            requested = min(requested, max(0.0, target_notional - current))
        if limits.enabled:
            verdict = assess_new_position(
                symbol_key,
                requested,
                equity,
                open_positions,
                canonical_history,
                limits,
            )
            adjusted = verdict.adjusted_notional
            reasons = list(verdict.reasons)
        else:
            adjusted = requested
            reasons = ["Portfolio correlation and volatility adjustments disabled."]
        concentration_room = (
            max(0.0, equity * max_symbol_concentration_pct / 100.0 - current)
            if max_symbol_concentration_pct > 0
            else float("inf")
        )
        shared_room = max(0.0, gross_limit - reserved)
        approved = min(adjusted, concentration_room, shared_room)
        if approved < adjusted:
            reasons.append(
                "Shared portfolio exposure or symbol concentration headroom clipped allocation."
            )
        status = BatchAllocationStatus.APPROVED if approved > 0 else BatchAllocationStatus.BLOCKED
        allocations.append(
            PortfolioAllocation(
                decision_id=intent.decision_id,
                symbol=intent.symbol,
                status=status,
                requested_notional_usd=request.requested_notional_usd,
                approved_notional_usd=approved,
                priority=priority,
                reasons=reasons,
            )
        )
        reserved += approved
        open_positions[symbol_key] = current + approved

    return PortfolioDecisionBatch(
        snapshot_hash=snapshot_hash(snapshot),
        snapshot_captured_at=snapshot.captured_at,
        account_equity_usd=equity,
        starting_gross_exposure_usd=starting_gross,
        starting_symbol_exposure_usd=starting_symbol_exposure,
        gross_limit_usd=configured_gross_limit,
        max_symbol_concentration_pct=(
            max_symbol_concentration_pct
            if max_symbol_concentration_pct > 0
            else None
        ),
        ending_reserved_exposure_usd=reserved,
        allocations=allocations,
    )
