from __future__ import annotations

from tradingagents.agents.schemas import TradeIntent

from .models import ExecutionPlan, ExecutionResult, PlanAction


class AlpacaExecutionGateway:
    name = "alpaca"

    def submit_plan(self, plan: ExecutionPlan, intent: TradeIntent) -> ExecutionResult:
        from tradingagents.dataflows.alpaca_utils import AlpacaUtils
        from tradingagents.agents.schemas import extract_protective_price

        actions = []
        idempotency_keys = plan.metadata.get("leg_idempotency_keys", [])
        for index, leg in enumerate(plan.legs):
            client_order_id = idempotency_keys[index] if index < len(idempotency_keys) else None
            if leg.action == PlanAction.HOLD:
                actions.append({"action": "hold", "result": {"success": True, "message": leg.reason}})
                continue
            if leg.action == PlanAction.CLOSE:
                result = AlpacaUtils.close_position(plan.symbol)
            else:
                controls = intent.risk_controls
                stop = controls.stop_loss_price or extract_protective_price(
                    controls.stop_loss,
                    entry_price=plan.reference_price,
                    is_stop_loss=True,
                )
                target = controls.take_profit_price or extract_protective_price(
                    controls.take_profit,
                    entry_price=plan.reference_price,
                    is_stop_loss=False,
                )
                protected = (
                    not leg.risk_reducing
                    and intent.execution_constraints.asset_class != "crypto"
                    and intent.execution_constraints.broker_protective_orders_enabled
                    and leg.quantity is not None
                    and int(leg.quantity) >= 1
                    and (stop or target)
                )
                if protected:
                    result = AlpacaUtils.place_protected_market_order(
                        plan.symbol,
                        leg.side or "buy",
                        qty=int(leg.quantity),
                        stop_loss_price=stop,
                        take_profit_price=target,
                        client_order_id=client_order_id,
                    )
                else:
                    result = AlpacaUtils.place_market_order(
                        plan.symbol,
                        leg.side or "buy",
                        notional=leg.notional_usd,
                        client_order_id=client_order_id,
                    )
            actions.append({"action": leg.action.value.lower(), "leg": leg.model_dump(mode="json"), "result": result})
            if not result.get("success"):
                return ExecutionResult(
                    success=False,
                    decision_id=plan.decision_id,
                    symbol=plan.symbol,
                    gateway=self.name,
                    plan=plan,
                    actions=actions,
                    error=result.get("error", "Broker rejected an execution leg."),
                )
        return ExecutionResult(
            success=True,
            decision_id=plan.decision_id,
            symbol=plan.symbol,
            gateway=self.name,
            plan=plan,
            actions=actions,
        )


class AlpacaPaperExecutionGateway(AlpacaExecutionGateway):
    name = "alpaca-paper"
