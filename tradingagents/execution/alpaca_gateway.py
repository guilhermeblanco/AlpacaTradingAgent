from __future__ import annotations

from tradingagents.agents.schemas import TradeIntent

from .models import ExecutionPlan, ExecutionResult, PlanAction
from .gateway import SubmissionUncertain
from .reconciliation import BrokerOrderSnapshot, BrokerOrderStatus


class AlpacaExecutionGateway:
    name = "alpaca"

    def close_position(self, symbol: str) -> dict:
        from tradingagents.dataflows.alpaca_utils import AlpacaUtils

        return AlpacaUtils.close_position(symbol)

    def get_order_snapshot(self, *, order_id=None, client_order_id=None) -> BrokerOrderSnapshot:
        from tradingagents.dataflows.alpaca_utils import get_alpaca_trading_client
        from alpaca.trading.requests import GetOrderByIdRequest

        client = get_alpaca_trading_client()
        if order_id:
            order = client.get_order_by_id(order_id, GetOrderByIdRequest(nested=True))
        elif client_order_id:
            order = client.get_order_by_client_id(client_order_id)
            order = client.get_order_by_id(order.id, GetOrderByIdRequest(nested=True))
        else:
            raise ValueError("order_id or client_order_id is required")

        def convert(value) -> BrokerOrderSnapshot:
            raw_status = str(getattr(getattr(value, "status", None), "value", getattr(value, "status", "unknown"))).lower()
            try:
                status = BrokerOrderStatus(raw_status)
            except ValueError:
                status = BrokerOrderStatus.UNKNOWN
            return BrokerOrderSnapshot(
                order_id=str(value.id),
                client_order_id=getattr(value, "client_order_id", None),
                symbol=str(value.symbol),
                side=str(getattr(getattr(value, "side", None), "value", getattr(value, "side", ""))),
                status=status,
                requested_quantity=float(value.qty) if getattr(value, "qty", None) is not None else None,
                filled_quantity=float(getattr(value, "filled_qty", 0) or 0),
                filled_avg_price=(float(value.filled_avg_price)
                                  if getattr(value, "filled_avg_price", None) is not None else None),
                child_orders=[convert(child) for child in (getattr(value, "legs", None) or [])],
            )

        return convert(order)

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
                result = AlpacaUtils.place_market_order(
                    plan.symbol,
                    leg.side or "sell",
                    notional=leg.notional_usd if leg.quantity is None else None,
                    qty=leg.quantity,
                    client_order_id=client_order_id,
                )
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
                    and plan.metadata.get("protection_mode", "native") == "native"
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
            if result.get("submission_uncertain"):
                raise SubmissionUncertain(
                    result.get("error", "Alpaca submission result is uncertain"),
                    gateway=self.name,
                    leg_index=index,
                    actions=actions,
                )
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
