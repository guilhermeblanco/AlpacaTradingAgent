from __future__ import annotations

import json
from typing import Callable, Optional
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from tradingagents.agents.schemas import TradeIntent
from tradingagents.execution.models import ExecutionPlan, ExecutionResult, PlanAction

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

    def submit_plan(self, plan: ExecutionPlan, intent: TradeIntent) -> ExecutionResult:
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
