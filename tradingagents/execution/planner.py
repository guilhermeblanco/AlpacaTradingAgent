from __future__ import annotations

from tradingagents.agents.schemas import IntentType, TargetPosition, TradeIntent, trade_intent_action
from tradingagents.broker.models import PortfolioSnapshot, QuoteSnapshot

from .models import ExecutionLeg, ExecutionPlan, PlanAction


class ExecutionPlanner:
    """Convert a model intent into deterministic broker-sized order legs."""

    def __init__(self, minimum_delta_usd: float = 1.0):
        self.minimum_delta_usd = max(0.0, float(minimum_delta_usd))

    def build_plan(
        self,
        intent: TradeIntent,
        portfolio: PortfolioSnapshot,
        quote: QuoteSnapshot,
        requested_notional_usd: float,
    ) -> ExecutionPlan:
        position = portfolio.position_for(intent.symbol)
        current_quantity = float(position.quantity) if position else 0.0
        current_value = float(position.market_value) if position else 0.0
        if position and position.quantity < 0 and current_value > 0:
            current_value = -current_value
        equity = portfolio.account.equity
        current_pct = (current_value / equity * 100.0) if equity else 0.0
        reference_price = quote.reference_price

        if intent.intent_type == IntentType.HOLD and not intent.schema_version.startswith("1"):
            return ExecutionPlan(
                decision_id=intent.decision_id,
                symbol=intent.symbol,
                intent_schema_version=intent.schema_version,
                current_allocation_pct=current_pct,
                target_allocation_pct=intent.target_portfolio_pct,
                current_notional_usd=current_value,
                target_notional_usd=current_value,
                delta_notional_usd=0.0,
                reference_price=reference_price,
                legs=[ExecutionLeg(action=PlanAction.HOLD, reason="Portfolio intent requested HOLD.")],
                metadata={"intent_type": intent.intent_type.value, **intent.metadata},
            )

        if intent.target_portfolio_pct is None:
            return self._legacy_plan(
                intent,
                current_value,
                current_pct,
                reference_price,
                requested_notional_usd,
                current_quantity,
            )

        target_value = equity * intent.target_portfolio_pct / 100.0
        if intent.target_position == TargetPosition.SHORT:
            target_value = -target_value
        elif intent.target_position == TargetPosition.NEUTRAL:
            target_value = 0.0
        delta = target_value - current_value
        increases_exposure = (
            abs(target_value) > abs(current_value)
            or (current_value != 0 and target_value != 0 and (current_value > 0) != (target_value > 0))
        )
        caps = [max(0.0, float(requested_notional_usd or 0.0))]
        if intent.max_notional_usd is not None:
            caps.append(max(0.0, float(intent.max_notional_usd)))
        cap = min(caps) if increases_exposure else None
        warnings: list[str] = []
        if cap is not None and abs(delta) > cap:
            delta = cap if delta > 0 else -cap
            target_value = current_value + delta
            warnings.append(f"Exposure-increasing delta capped at ${cap:,.2f}.")

        legs = self._target_legs(
            current_value,
            target_value,
            delta,
            reference_price,
            current_quantity,
        )
        return ExecutionPlan(
            decision_id=intent.decision_id,
            symbol=intent.symbol,
            intent_schema_version=intent.schema_version,
            current_allocation_pct=current_pct,
            target_allocation_pct=intent.target_portfolio_pct,
            current_notional_usd=current_value,
            target_notional_usd=target_value,
            delta_notional_usd=delta,
            reference_price=reference_price,
            legs=legs,
            warnings=warnings,
            metadata={"intent_type": intent.intent_type.value, **intent.metadata},
        )

    def _target_legs(self, current, target, delta, price, current_quantity):
        if abs(delta) < self.minimum_delta_usd:
            return [ExecutionLeg(action=PlanAction.HOLD, reason="Allocation already at target.")]
        if target == 0 and current != 0:
            return [
                ExecutionLeg(
                    action=PlanAction.CLOSE,
                    side="sell" if current > 0 else "buy",
                    notional_usd=abs(current),
                    quantity=abs(current_quantity) or None,
                    risk_reducing=True,
                    reason="Target allocation is zero.",
                )
            ]

        legs = []
        crosses_zero = current != 0 and target != 0 and (current > 0) != (target > 0)
        if crosses_zero:
            legs.append(
                ExecutionLeg(
                    action=PlanAction.CLOSE,
                    side="sell" if current > 0 else "buy",
                    notional_usd=abs(current),
                    quantity=abs(current_quantity) or None,
                    risk_reducing=True,
                    reason="Close existing exposure before reversing direction.",
                )
            )
            delta = target

        side = "buy" if delta > 0 else "sell"
        risk_reducing = not crosses_zero and abs(target) < abs(current)
        notional = abs(delta)
        legs.append(
            ExecutionLeg(
                action=PlanAction.BUY if side == "buy" else PlanAction.SELL,
                side=side,
                notional_usd=notional,
                quantity=(notional / price) if price else None,
                risk_reducing=risk_reducing,
                reason="Move current exposure toward target allocation.",
            )
        )
        return legs

    def _legacy_plan(
        self, intent, current_value, current_pct, price, requested, current_quantity
    ):
        action = trade_intent_action(intent) or "HOLD"
        requested = max(0.0, float(requested or 0.0))
        if intent.max_notional_usd is not None:
            requested = min(requested, intent.max_notional_usd)

        if action in {"HOLD", "NEUTRAL"}:
            legs = [ExecutionLeg(action=PlanAction.HOLD, reason="Intent requested no order.")]
            delta = 0.0
        elif action == "SELL" and current_value > 0:
            legs = [ExecutionLeg(action=PlanAction.CLOSE, side="sell", notional_usd=abs(current_value), quantity=abs(current_quantity) or None, risk_reducing=True, reason="Legacy SELL closes the long position.")]
            delta = -current_value
        else:
            side = "sell" if action == "SHORT" else "buy"
            signed = -requested if side == "sell" else requested
            legs = [ExecutionLeg(action=PlanAction.SELL if side == "sell" else PlanAction.BUY, side=side, notional_usd=requested, quantity=(requested / price) if price else None, reason="Legacy direction intent uses configured notional.")]
            delta = signed
        return ExecutionPlan(
            decision_id=intent.decision_id,
            symbol=intent.symbol,
            intent_schema_version=intent.schema_version,
            current_allocation_pct=current_pct,
            current_notional_usd=current_value,
            delta_notional_usd=delta,
            reference_price=price,
            legs=legs,
            warnings=["Intent has no target_portfolio_pct; legacy notional semantics applied."],
            metadata={
                "intent_type": intent.intent_type.value,
                "legacy_action": action,
                **intent.metadata,
            },
        )
