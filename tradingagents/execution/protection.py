"""Capability-driven protective-order policy."""

from __future__ import annotations

from enum import Enum

from tradingagents.agents.schemas import TradeIntent, extract_protective_price
from tradingagents.broker.registry import BrokerCapabilities

from .models import ExecutionPlan, PlanAction


class ProtectionMode(str, Enum):
    NONE = "none"
    NATIVE = "native"
    SOFTWARE = "software"


def protective_prices(intent: TradeIntent, reference_price: float | None) -> dict:
    controls = intent.risk_controls
    return {
        "stop_loss_price": controls.stop_loss_price
        or extract_protective_price(
            controls.stop_loss, entry_price=reference_price, is_stop_loss=True
        ),
        "take_profit_price": controls.take_profit_price
        or extract_protective_price(
            controls.take_profit, entry_price=reference_price, is_stop_loss=False
        ),
    }


def select_protection_mode(
    intent: TradeIntent,
    plan: ExecutionPlan,
    capabilities: BrokerCapabilities | None,
) -> tuple[ProtectionMode, dict]:
    prices = protective_prices(intent, plan.reference_price)
    if not any(prices.values()) or plan.is_noop:
        return ProtectionMode.NONE, prices
    opening_legs = [
        leg
        for leg in plan.legs
        if leg.action != PlanAction.HOLD and not leg.risk_reducing
    ]
    native_quantity = all(
        leg.quantity is not None
        and int(leg.quantity) >= 1
        and abs(leg.quantity - int(leg.quantity)) < 1e-9
        for leg in opening_legs
    )
    if (
        capabilities is not None
        and capabilities.native_brackets
        and intent.execution_constraints.asset_class != "crypto"
        and native_quantity
    ):
        return ProtectionMode.NATIVE, prices
    return ProtectionMode.SOFTWARE, prices
