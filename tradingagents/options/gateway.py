from __future__ import annotations

from typing import Protocol

from .models import OptionsExecutionResult, OptionsTradeIntent


class OptionsExecutionGateway(Protocol):
    name: str

    def submit(self, intent: OptionsTradeIntent, *, client_order_id: str) -> OptionsExecutionResult:
        ...


class AlpacaOptionsGateway:
    name = "alpaca-options"

    def submit(self, intent: OptionsTradeIntent, *, client_order_id: str) -> OptionsExecutionResult:
        from alpaca.trading.enums import OrderClass, OrderSide, PositionIntent, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest
        from tradingagents.dataflows.alpaca_utils import get_alpaca_trading_client

        legs = []
        for leg in intent.legs:
            position_intent = PositionIntent(leg.position_intent.value)
            side = OrderSide.BUY if position_intent in {
                PositionIntent.BUY_TO_OPEN, PositionIntent.BUY_TO_CLOSE,
            } else OrderSide.SELL
            legs.append(OptionLegRequest(
                symbol=leg.symbol.upper(), ratio_qty=leg.ratio_quantity,
                side=side, position_intent=position_intent,
            ))
        if len(legs) == 1:
            request = LimitOrderRequest(
                symbol=legs[0].symbol, qty=intent.quantity, side=legs[0].side,
                position_intent=legs[0].position_intent, time_in_force=TimeInForce.DAY,
                limit_price=intent.limit_price, client_order_id=client_order_id,
            )
        else:
            request = LimitOrderRequest(
                qty=intent.quantity, order_class=OrderClass.MLEG, legs=legs,
                time_in_force=TimeInForce.DAY, limit_price=intent.limit_price,
                client_order_id=client_order_id,
            )
        try:
            order = get_alpaca_trading_client().submit_order(request)
            return OptionsExecutionResult(
                success=True, decision_id=intent.decision_id, underlying=intent.underlying,
                gateway=self.name, intent=intent, broker_order_id=str(order.id),
                client_order_id=getattr(order, "client_order_id", client_order_id),
                status=str(getattr(getattr(order, "status", None), "value", getattr(order, "status", ""))),
            )
        except Exception as exc:
            return OptionsExecutionResult(
                success=False, decision_id=intent.decision_id, underlying=intent.underlying,
                gateway=self.name, intent=intent, client_order_id=client_order_id, error=str(exc),
            )
