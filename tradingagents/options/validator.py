from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from .models import OptionPositionIntent, OptionsTradeIntent


class OptionsRiskPolicy(BaseModel):
    enabled: bool = False
    paper_only: bool = True
    max_contracts: int = Field(default=2, ge=1)
    max_loss_usd: float = Field(default=500.0, gt=0)
    min_days_to_expiration: int = Field(default=7, ge=1)
    max_days_to_expiration: int = Field(default=60, ge=1)
    min_open_interest: int = Field(default=100, ge=0)
    max_bid_ask_spread_pct: float = Field(default=15.0, ge=0)
    allow_undefined_risk: bool = False
    allowed_strategies: set[str] = Field(default_factory=lambda: {
        "long_call", "long_put", "debit_spread", "credit_spread", "iron_condor",
    })


def deterministic_max_loss_usd(intent: OptionsTradeIntent) -> float:
    """Compute defined worst-case loss from net price and strike geometry."""
    strategy = intent.strategy.lower()
    multiplier = 100.0 * intent.quantity
    if strategy in {"long_call", "long_put", "debit_spread"}:
        return intent.limit_price * multiplier
    if strategy == "credit_spread":
        if len(intent.legs) != 2:
            raise ValueError("credit_spread requires exactly two legs")
        contracts = [leg.contract for leg in intent.legs]
        if len({row["contract_type"] for row in contracts}) != 1:
            raise ValueError("credit_spread legs must use the same option type")
        width = abs(contracts[0]["strike"] - contracts[1]["strike"])
        return max(0.0, (width - intent.limit_price) * multiplier)
    if strategy == "iron_condor":
        if len(intent.legs) != 4:
            raise ValueError("iron_condor requires exactly four legs")
        contracts = [leg.contract for leg in intent.legs]
        calls = sorted(row["strike"] for row in contracts if row["contract_type"] == "call")
        puts = sorted(row["strike"] for row in contracts if row["contract_type"] == "put")
        if len(calls) != 2 or len(puts) != 2:
            raise ValueError("iron_condor requires two call and two put legs")
        widest_wing = max(calls[1] - calls[0], puts[1] - puts[0])
        return max(0.0, (widest_wing - intent.limit_price) * multiplier)
    raise ValueError(f"No deterministic loss model for strategy {intent.strategy!r}")


def validate_options_intent(
    intent: OptionsTradeIntent,
    policy: OptionsRiskPolicy,
    *,
    today: date | None = None,
    is_paper: bool = True,
) -> list[str]:
    errors: list[str] = []
    today = today or date.today()
    if not policy.enabled:
        errors.append("Autonomous options execution is disabled")
    if policy.paper_only and not is_paper:
        errors.append("Options policy permits paper trading only")
    if intent.strategy.lower() not in policy.allowed_strategies:
        errors.append(f"Strategy {intent.strategy!r} is not allowed")
    if intent.quantity > policy.max_contracts:
        errors.append("Contract quantity exceeds policy maximum")
    if intent.max_loss_usd > policy.max_loss_usd:
        errors.append("Maximum loss exceeds policy limit")
    try:
        computed_max_loss = deterministic_max_loss_usd(intent)
        if computed_max_loss > policy.max_loss_usd:
            errors.append("Deterministic maximum loss exceeds policy limit")
        if intent.max_loss_usd + 0.01 < computed_max_loss:
            errors.append("Declared maximum loss understates deterministic maximum loss")
    except ValueError as exc:
        errors.append(str(exc))
    underlyings = {leg.contract["underlying"] for leg in intent.legs}
    if underlyings != {intent.underlying}:
        errors.append("All option legs must match the declared underlying")
    expirations = {leg.contract["expiration"] for leg in intent.legs}
    for expiration in expirations:
        dte = (expiration - today).days
        if dte < policy.min_days_to_expiration or dte > policy.max_days_to_expiration:
            errors.append(f"Expiration {expiration.isoformat()} is outside allowed DTE range")
    opening_shorts = [leg for leg in intent.legs if leg.position_intent == OptionPositionIntent.SELL_TO_OPEN]
    opening_longs = [leg for leg in intent.legs if leg.position_intent == OptionPositionIntent.BUY_TO_OPEN]
    if opening_shorts and not opening_longs and not policy.allow_undefined_risk:
        errors.append("Undefined-risk short options are disabled")
    for leg in intent.legs:
        if leg.open_interest is None or leg.open_interest < policy.min_open_interest:
            errors.append(f"{leg.symbol} does not meet minimum open interest")
        if leg.bid_price is None or leg.ask_price is None or leg.ask_price <= 0:
            errors.append(f"{leg.symbol} is missing a usable bid/ask quote")
        else:
            midpoint = (leg.bid_price + leg.ask_price) / 2
            spread_pct = ((leg.ask_price - leg.bid_price) / midpoint * 100) if midpoint else float("inf")
            if spread_pct > policy.max_bid_ask_spread_pct:
                errors.append(f"{leg.symbol} bid/ask spread exceeds policy maximum")
    return list(dict.fromkeys(errors))
