from __future__ import annotations

from tradingagents.agents.schemas import TargetPosition, TradeIntent
from tradingagents.broker.models import PortfolioSnapshot

from .models import ExecutionPlan, PlanAction


def validate_intent(intent: TradeIntent, execution_symbol: str) -> list[str]:
    errors: list[str] = []
    expected = (execution_symbol or "").upper().replace("/", "")
    actual = (intent.symbol or "").upper().replace("/", "")
    if not actual or actual != expected:
        errors.append(f"Intent symbol {intent.symbol!r} does not match {execution_symbol!r}.")
    action = intent.action.value
    allowed = (
        {"LONG", "NEUTRAL", "SHORT"}
        if intent.trading_mode == "trading"
        else {"BUY", "HOLD", "SELL"}
    )
    if action not in allowed:
        errors.append(f"Action {action} is invalid for {intent.trading_mode} mode.")
    if intent.target_position == TargetPosition.SHORT:
        if not intent.execution_constraints.allow_shorts:
            errors.append("Short exposure is disabled for this intent.")
        if intent.execution_constraints.asset_class == "crypto":
            errors.append("Crypto short exposure is unsupported by spot execution.")
    if intent.target_portfolio_pct is not None and intent.target_position == TargetPosition.NEUTRAL and intent.target_portfolio_pct != 0:
        errors.append("A neutral target position must have a zero target allocation.")
    if intent.intent_type.value == "REDUCE" and intent.target_portfolio_pct is None:
        errors.append("A REDUCE intent requires target_portfolio_pct.")
    return errors


def validate_snapshot(intent: TradeIntent, portfolio: PortfolioSnapshot) -> list[str]:
    if intent.schema_version.startswith("1"):
        return []
    position = portfolio.position_for(intent.symbol)
    live_side = position.side if position else "NEUTRAL"
    if live_side != intent.current_position.value:
        return [
            f"Portfolio changed after decision: intent expected {intent.current_position.value}, live position is {live_side}."
        ]
    return []


def validate_plan_semantics(intent: TradeIntent, plan: ExecutionPlan) -> list[str]:
    actionable = [leg for leg in plan.legs if leg.action != PlanAction.HOLD]
    operation = intent.intent_type.value
    if operation == "HOLD" and actionable:
        return ["HOLD intent produced an actionable execution plan."]
    if operation == "CLOSE" and any(leg.action != PlanAction.CLOSE for leg in actionable):
        return ["CLOSE intent produced a non-close execution leg."]
    if operation == "REDUCE" and any(not leg.risk_reducing for leg in actionable):
        return ["REDUCE intent would increase or reverse exposure."]
    if operation in {"OPEN", "INCREASE"} and any(leg.risk_reducing for leg in actionable):
        return [f"{operation} intent would reduce existing exposure."]
    return []
