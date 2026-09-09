"""Reconcile terminal order fills against the broker account position."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from tradingagents.broker.models import PortfolioSnapshot

from .models import ExecutionPlan
from .reconciliation import ReconciliationReport


class AccountReconciliationReport(BaseModel):
    decision_id: str
    broker: str
    symbol: str
    checked: bool
    matched: bool
    pre_trade_quantity: Optional[float] = None
    expected_quantity: Optional[float] = None
    actual_quantity: Optional[float] = None
    difference: Optional[float] = None
    problems: list[str] = Field(default_factory=list)


def reconcile_account_position(
    plan: ExecutionPlan,
    order_report: ReconciliationReport,
    portfolio: PortfolioSnapshot,
    *,
    quantity_tolerance: float = 1e-6,
) -> AccountReconciliationReport:
    raw_pre_trade = plan.metadata.get("pre_trade_quantity")
    if raw_pre_trade is None:
        return AccountReconciliationReport(
            decision_id=plan.decision_id,
            broker=portfolio.broker,
            symbol=plan.symbol,
            checked=False,
            matched=True,
            problems=["Execution plan predates pre-trade position capture."],
        )

    pre_trade = float(raw_pre_trade)
    expected = pre_trade
    for reconciled_leg in order_report.legs:
        leg = plan.legs[reconciled_leg.leg_index]
        signed_fill = float(reconciled_leg.filled_quantity)
        if str(leg.side or "").lower() == "sell":
            signed_fill = -signed_fill
        expected += signed_fill

    position = portfolio.position_for(plan.symbol)
    actual = float(position.quantity) if position is not None else 0.0
    difference = actual - expected
    matched = abs(difference) <= max(0.0, float(quantity_tolerance))
    problems = [] if matched else [
        (
            f"Broker position {actual:g} does not match expected position "
            f"{expected:g} after terminal fills."
        )
    ]
    return AccountReconciliationReport(
        decision_id=plan.decision_id,
        broker=portfolio.broker,
        symbol=plan.symbol,
        checked=True,
        matched=matched,
        pre_trade_quantity=pre_trade,
        expected_quantity=expected,
        actual_quantity=actual,
        difference=difference,
        problems=problems,
    )
