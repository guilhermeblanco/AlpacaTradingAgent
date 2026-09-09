from __future__ import annotations

import json
from typing import Callable, Optional
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from tradingagents.agents.schemas import TradeIntent
from tradingagents.execution.models import ExecutionPlan, ExecutionResult, PlanAction
from tradingagents.execution.reconciliation import (
    BrokerOrderSnapshot,
    BrokerOrderStatus,
)

from .models import AccountSnapshot, PortfolioSnapshot, PositionSnapshot, QuoteSnapshot


class TradierClient:
    def __init__(self, *, token: str, account_id: str, sandbox: bool = True,
                 transport: Optional[Callable[..., dict]] = None):
        if not token or not account_id:
            raise ValueError("Tradier token and account ID are required")
        self.token = token
        self.account_id = account_id
        self.sandbox = sandbox
        self.base_url = "https://sandbox.tradier.com/v1" if sandbox else "https://api.tradier.com/v1"
        self.transport = transport or self._http

    def request(self, method: str, path: str, *, params: dict | None = None,
                data: dict | None = None) -> dict:
        return self.transport(method, path, params=params, data=data)

    def _http(self, method: str, path: str, *, params=None, data=None) -> dict:
        url = self.base_url + path
        if params:
            url += "?" + urlencode(params)
        body = urlencode(data).encode() if data is not None else None
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = Request(url, data=body, headers=headers, method=method.upper())
        try:
            with urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Tradier HTTP {exc.code}: {detail}") from exc


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


class TradierSnapshotProvider:
    def __init__(self, client: TradierClient):
        self.client = client

    def get_quote_snapshot(self, symbol: str) -> QuoteSnapshot:
        response = self.client.request("GET", "/markets/quotes", params={"symbols": symbol})
        quote = response.get("quotes", {}).get("quote") or {}
        if isinstance(quote, list):
            quote = quote[0]
        return QuoteSnapshot(
            symbol=symbol, bid_price=quote.get("bid"), ask_price=quote.get("ask"),
            last_price=quote.get("last") or quote.get("close"),
        )

    def get_portfolio_snapshot(self) -> PortfolioSnapshot:
        account_id = self.client.account_id
        balances = self.client.request("GET", f"/accounts/{account_id}/balances").get("balances", {})
        response = self.client.request("GET", f"/accounts/{account_id}/positions")
        raw_container = response.get("positions") or {}
        raw_positions = _as_list(raw_container.get("position") if isinstance(raw_container, dict) else None)
        positions = []
        for raw in raw_positions:
            quantity = float(raw.get("quantity") or 0)
            quote = self.get_quote_snapshot(raw["symbol"])
            current_price = quote.reference_price
            market_value = float(raw.get("market_value") or ((current_price or 0) * quantity))
            positions.append(PositionSnapshot(
                symbol=raw["symbol"], quantity=quantity, market_value=market_value,
                average_entry_price=(float(raw["cost_basis"]) / abs(quantity)
                                     if quantity and raw.get("cost_basis") is not None else None),
                current_price=current_price, asset_class="equity",
            ))
        equity = float(balances.get("total_equity") or balances.get("equity") or 0)
        raw_cash = balances.get("cash")
        nested_cash = raw_cash.get("cash_available", 0) if isinstance(raw_cash, dict) else 0
        cash = float(balances.get("total_cash") or nested_cash or 0)
        buying_power = float(balances.get("stock_buying_power") or balances.get("buying_power") or cash)
        return PortfolioSnapshot(
            broker="tradier",
            account=AccountSnapshot(equity=equity, cash=cash, buying_power=buying_power),
            positions=positions,
        )


class TradierExecutionGateway:
    name = "tradier"

    def __init__(self, client: TradierClient):
        self.client = client

    def close_position(self, symbol: str) -> dict:
        position = self._position(symbol)
        if position is None:
            return {"success": True, "status": "already_closed", "symbol": symbol}
        side = "sell" if position.quantity > 0 else "buy_to_cover"
        response = self.client.request(
            "POST",
            f"/accounts/{self.client.account_id}/orders",
            data={
                "class": "equity",
                "symbol": symbol,
                "side": side,
                "quantity": int(abs(position.quantity)),
                "type": "market",
                "duration": "day",
            },
        )
        order = response.get("order") or {}
        success = bool(order.get("id") or order.get("result"))
        return {"success": success, "order_id": order.get("id"), "raw": order}

    def _position(self, symbol: str):
        return TradierSnapshotProvider(self.client).get_portfolio_snapshot().position_for(
            symbol
        )

    def get_order_snapshot(
        self, *, order_id=None, client_order_id=None
    ) -> BrokerOrderSnapshot:
        if order_id:
            response = self.client.request(
                "GET",
                f"/accounts/{self.client.account_id}/orders/{order_id}",
            )
            raw = response.get("order") or {}
        elif client_order_id:
            response = self.client.request(
                "GET",
                f"/accounts/{self.client.account_id}/orders",
                params={"includeTags": "true", "limit": 1500},
            )
            container = response.get("orders") or {}
            orders = _as_list(
                container.get("order") if isinstance(container, dict) else None
            )
            raw = next(
                (
                    row
                    for row in orders
                    if str(row.get("tag") or "") == str(client_order_id)
                ),
                None,
            )
            if raw is None:
                raise KeyError(f"Tradier order tag {client_order_id} was not found")
        else:
            raise ValueError("order_id or client_order_id is required")
        if not isinstance(raw, dict) or not raw.get("id"):
            raise RuntimeError("Tradier order response is missing an order")
        statuses = {
            "pending": BrokerOrderStatus.NEW,
            "open": BrokerOrderStatus.NEW,
            "partially_filled": BrokerOrderStatus.PARTIALLY_FILLED,
            "filled": BrokerOrderStatus.FILLED,
            "canceled": BrokerOrderStatus.CANCELED,
            "cancelled": BrokerOrderStatus.CANCELED,
            "rejected": BrokerOrderStatus.REJECTED,
            "error": BrokerOrderStatus.REJECTED,
            "expired": BrokerOrderStatus.EXPIRED,
        }
        raw_side = str(raw.get("side") or "").lower()
        side = "buy" if raw_side in {"buy", "buy_to_cover"} else "sell"
        filled_quantity = float(
            raw.get("exec_quantity") or raw.get("executed_quantity") or 0
        )
        fill_price = float(raw.get("avg_fill_price") or 0) or None
        return BrokerOrderSnapshot(
            order_id=str(raw["id"]),
            client_order_id=raw.get("tag"),
            symbol=str(raw.get("symbol") or ""),
            side=side,
            status=statuses.get(
                str(raw.get("status") or "").lower(), BrokerOrderStatus.UNKNOWN
            ),
            requested_quantity=(
                float(raw["quantity"]) if raw.get("quantity") is not None else None
            ),
            filled_quantity=filled_quantity,
            filled_avg_price=fill_price,
            child_orders=[
                self._snapshot_from_child(child)
                for child in _as_list(raw.get("leg"))
            ],
        )

    @staticmethod
    def _snapshot_from_child(raw: dict) -> BrokerOrderSnapshot:
        statuses = {
            "open": BrokerOrderStatus.NEW,
            "partially_filled": BrokerOrderStatus.PARTIALLY_FILLED,
            "filled": BrokerOrderStatus.FILLED,
            "canceled": BrokerOrderStatus.CANCELED,
            "expired": BrokerOrderStatus.EXPIRED,
            "rejected": BrokerOrderStatus.REJECTED,
        }
        side = "buy" if str(raw.get("side") or "").lower().startswith("buy") else "sell"
        return BrokerOrderSnapshot(
            order_id=str(raw.get("id") or ""),
            symbol=str(raw.get("symbol") or raw.get("option_symbol") or ""),
            side=side,
            status=statuses.get(
                str(raw.get("status") or "").lower(), BrokerOrderStatus.UNKNOWN
            ),
            requested_quantity=(
                float(raw["quantity"]) if raw.get("quantity") is not None else None
            ),
            filled_quantity=float(
                raw.get("exec_quantity") or raw.get("executed_quantity") or 0
            ),
            filled_avg_price=float(raw.get("avg_fill_price") or 0) or None,
        )

    def submit_plan(self, plan: ExecutionPlan, intent: TradeIntent) -> ExecutionResult:
        from tradingagents.execution.gateway import (
            SubmissionUncertain,
            is_uncertain_submission_error,
        )

        actions = []
        keys = plan.metadata.get("leg_idempotency_keys", [])
        for index, leg in enumerate(plan.legs):
            if leg.action == PlanAction.HOLD:
                actions.append({"action": "hold", "result": {"success": True}})
                continue
            quantity = leg.quantity
            if quantity is None and plan.reference_price:
                quantity = leg.notional_usd / plan.reference_price
            whole_quantity = int(quantity or 0)
            if whole_quantity < 1:
                return ExecutionResult(
                    success=False, decision_id=plan.decision_id, symbol=plan.symbol,
                    gateway=self.name, plan=plan, actions=actions,
                    error="Tradier equity orders require at least one whole share",
                )
            side = leg.side or "buy"
            if side == "sell" and not leg.risk_reducing and intent.target_position.value == "SHORT":
                side = "sell_short"
            elif side == "buy" and leg.risk_reducing:
                side = "buy_to_cover"
            payload = {
                "class": "equity", "symbol": plan.symbol, "side": side,
                "quantity": whole_quantity, "type": "market", "duration": "day",
                "tag": keys[index] if index < len(keys) else plan.decision_id,
            }
            try:
                response = self.client.request(
                    "POST", f"/accounts/{self.client.account_id}/orders", data=payload
                )
                order = response.get("order") or {}
                success = bool(order.get("id") or order.get("result")) and order.get("status") != "error"
                result = {
                    "success": success, "order_id": order.get("id"),
                    "status": order.get("status"), "client_order_id": payload["tag"], "raw": order,
                }
            except Exception as exc:
                if is_uncertain_submission_error(exc):
                    result = {
                        "success": False,
                        "status": "unknown",
                        "client_order_id": payload["tag"],
                        "submission_uncertain": True,
                        "error": str(exc),
                    }
                    actions.append({
                        "action": leg.action.value.lower(),
                        "leg": leg.model_dump(mode="json"),
                        "result": result,
                    })
                    raise SubmissionUncertain(
                        f"Tradier submission may have been accepted: {exc}",
                        gateway=self.name,
                        leg_index=index,
                        actions=actions,
                    ) from exc
                result = {"success": False, "error": str(exc)}
            actions.append({
                "action": leg.action.value.lower(), "leg": leg.model_dump(mode="json"), "result": result,
            })
            if not result["success"]:
                return ExecutionResult(
                    success=False, decision_id=plan.decision_id, symbol=plan.symbol,
                    gateway=self.name, plan=plan, actions=actions,
                    error=result.get("error", "Tradier rejected the order"),
                )
        return ExecutionResult(
            success=True, decision_id=plan.decision_id, symbol=plan.symbol,
            gateway=self.name, plan=plan, actions=actions,
        )
