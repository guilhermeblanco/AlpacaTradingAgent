from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from tradingagents.agents.schemas import TradeIntent
from tradingagents.execution.models import ExecutionPlan, ExecutionResult, PlanAction
from tradingagents.execution.reconciliation import (
    BrokerOrderSnapshot,
    BrokerOrderStatus,
)

from .models import AccountSnapshot, PortfolioSnapshot, PositionSnapshot, QuoteSnapshot


DEFAULT_ROBINHOOD_MCP_URL = "https://agent.robinhood.com/mcp/trading"


class RobinhoodMCPClient:
    """Minimal streamable-HTTP MCP client for Robinhood's trading endpoint."""

    def __init__(self, *, access_token: str, mcp_url: str = DEFAULT_ROBINHOOD_MCP_URL,
                 timeout_seconds: float = 20, transport: Optional[Callable[..., Any]] = None):
        if not access_token:
            raise ValueError("Robinhood MCP access token is required")
        self.access_token = access_token
        self.mcp_url = mcp_url
        self.timeout_seconds = timeout_seconds
        self.transport = transport or requests.post
        self.session_id: Optional[str] = None
        self._next_id = 1

    def _id(self) -> int:
        value = self._next_id
        self._next_id += 1
        return value

    @staticmethod
    def _decode(response) -> dict:
        text = response.text.strip()
        if not text:
            return {}
        if text.startswith("event:") or "\ndata:" in text:
            data_lines = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
            text = data_lines[-1] if data_lines else "{}"
        value = json.loads(text)
        if not isinstance(value, dict):
            raise RuntimeError("Robinhood MCP response must be an object")
        return value

    def _post(self, payload: dict) -> dict:
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        response = self.transport(
            self.mcp_url, json=payload, headers=headers, timeout=self.timeout_seconds
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Robinhood MCP HTTP {response.status_code}: {response.text[:300]}")
        self.session_id = response.headers.get("Mcp-Session-Id", self.session_id)
        return self._decode(response)

    def initialize(self) -> None:
        self._post({
            "jsonrpc": "2.0", "id": self._id(), "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "AlpacaTradingAgent", "version": "0.1.0"}},
        })
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def call_tool(self, name: str, arguments: dict | None = None) -> Any:
        if not self.session_id:
            self.initialize()
        response = self._post({
            "jsonrpc": "2.0", "id": self._id(), "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        })
        if response.get("error"):
            raise RuntimeError(str(response["error"]))
        result = response.get("result") or {}
        if result.get("isError"):
            raise RuntimeError("Robinhood MCP tool returned an error")
        structured = result.get("structuredContent")
        if structured is not None:
            return structured.get("data", structured) if isinstance(structured, dict) else structured
        content = result.get("content") or []
        text = "\n".join(item.get("text", "") for item in content if isinstance(item, dict))
        if text:
            decoded = json.loads(text)
            return decoded.get("data", decoded) if isinstance(decoded, dict) else decoded
        return result


def load_robinhood_access_token(*, access_token: Optional[str] = None,
                                token_path: Optional[str] = None) -> str:
    if access_token:
        return access_token
    if not token_path:
        raise ValueError("ROBINHOOD_MCP_ACCESS_TOKEN or ROBINHOOD_MCP_TOKEN_PATH is required")
    payload = json.loads(Path(token_path).expanduser().read_text(encoding="utf-8"))
    token = payload.get("access_token")
    if not token:
        raise ValueError("Robinhood token file does not contain access_token")
    return str(token)


def _rows(value: Any, *keys: str) -> list[dict]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        for key in keys:
            if isinstance(value.get(key), list):
                return [row for row in value[key] if isinstance(row, dict)]
    return []


class RobinhoodSnapshotProvider:
    def __init__(self, client: RobinhoodMCPClient, *, account_number: Optional[str] = None):
        self.client = client
        self.account_number = account_number

    def _account(self) -> dict:
        accounts = _rows(self.client.call_tool("get_accounts", {}), "accounts", "results")
        if self.account_number:
            selected = next((row for row in accounts if row.get("account_number") == self.account_number), None)
        else:
            eligible = [row for row in accounts if row.get("state") == "active" and row.get("agentic_allowed") is True]
            selected = eligible[0] if eligible else None
        if not selected:
            raise RuntimeError("No active Robinhood agentic account is available")
        return selected

    def get_quote_snapshot(self, symbol: str) -> QuoteSnapshot:
        response = self.client.call_tool("get_equity_quotes", {"symbols": [symbol.upper()]})
        rows = _rows(response, "results", "quotes")
        if not rows:
            raise RuntimeError(f"Robinhood quote unavailable for {symbol}")
        quote = rows[0]
        return QuoteSnapshot(
            symbol=symbol, bid_price=quote.get("bid_price") or quote.get("bid"),
            ask_price=quote.get("ask_price") or quote.get("ask"),
            last_price=quote.get("last_trade_price") or quote.get("last_price") or quote.get("price"),
        )

    def get_portfolio_snapshot(self) -> PortfolioSnapshot:
        account = self._account()
        account_number = str(account["account_number"])
        portfolio = self.client.call_tool("get_portfolio", {"account_number": account_number})
        raw_positions = self.client.call_tool("get_equity_positions", {"account_number": account_number})
        positions = []
        for raw in _rows(raw_positions, "positions", "results"):
            quantity = float(raw.get("quantity") or raw.get("qty") or 0)
            price = float(raw.get("price") or raw.get("current_price") or 0) or None
            positions.append(PositionSnapshot(
                symbol=str(raw["symbol"]), quantity=quantity,
                market_value=float(raw.get("market_value") or ((price or 0) * quantity)),
                average_entry_price=float(raw.get("average_price") or raw.get("average_entry_price") or 0) or None,
                current_price=price, asset_class="equity",
            ))
        buying = portfolio.get("buying_power") or {}
        if not isinstance(buying, dict):
            buying = {}
        return PortfolioSnapshot(
            broker="robinhood",
            account=AccountSnapshot(
                equity=float(portfolio.get("total_value") or portfolio.get("equity") or 0),
                cash=float(portfolio.get("cash") or 0),
                buying_power=float(buying.get("buying_power") or portfolio.get("buying_power") or 0),
            ),
            positions=positions,
        )


class RobinhoodExecutionGateway:
    name = "robinhood"

    def __init__(self, client: RobinhoodMCPClient, *, account_number: Optional[str] = None,
                 review_only: bool = True, live_orders_enabled: bool = False):
        self.client = client
        self.provider = RobinhoodSnapshotProvider(client, account_number=account_number)
        self.review_only = review_only
        self.live_orders_enabled = live_orders_enabled

    def close_position(self, symbol: str) -> dict:
        if self.review_only or not self.live_orders_enabled:
            return {
                "success": False,
                "error": "Robinhood software stops require explicitly enabled live orders",
            }
        account = self.provider._account()
        position = self.provider.get_portfolio_snapshot().position_for(symbol)
        if position is None:
            return {"success": True, "status": "already_closed", "symbol": symbol}
        arguments = {
            "account_number": account["account_number"],
            "symbol": symbol.upper(),
            "side": "sell",
            "type": "market",
            "dollar_amount": f"{abs(position.market_value):.2f}",
            "market_hours": "regular_hours",
        }
        self.client.call_tool("review_equity_order", arguments)
        result = self.client.call_tool("place_equity_order", arguments)
        return {
            "success": bool(isinstance(result, dict) and result.get("order_id")),
            "order_id": result.get("order_id") if isinstance(result, dict) else None,
            "raw": result,
        }

    def get_order_snapshot(
        self, *, order_id=None, client_order_id=None
    ) -> BrokerOrderSnapshot:
        if not order_id and not client_order_id:
            raise ValueError("order_id or client_order_id is required")
        account = self.provider._account()
        response = self.client.call_tool(
            "get_equity_orders",
            {"account_number": account["account_number"]},
        )
        orders = _rows(response, "orders", "results")
        raw = next(
            (
                row
                for row in orders
                if (
                    order_id
                    and str(row.get("order_id") or row.get("id") or "")
                    == str(order_id)
                )
                or (
                    client_order_id
                    and str(
                        row.get("ref_id")
                        or row.get("client_order_id")
                        or row.get("client_id")
                        or ""
                    )
                    == str(client_order_id)
                )
            ),
            None,
        )
        if raw is None:
            key = order_id or client_order_id
            raise KeyError(f"Robinhood equity order {key} was not found")
        statuses = {
            "queued": BrokerOrderStatus.NEW,
            "pending": BrokerOrderStatus.NEW,
            "confirmed": BrokerOrderStatus.NEW,
            "open": BrokerOrderStatus.NEW,
            "partially_filled": BrokerOrderStatus.PARTIALLY_FILLED,
            "filled": BrokerOrderStatus.FILLED,
            "canceled": BrokerOrderStatus.CANCELED,
            "cancelled": BrokerOrderStatus.CANCELED,
            "rejected": BrokerOrderStatus.REJECTED,
            "failed": BrokerOrderStatus.REJECTED,
            "expired": BrokerOrderStatus.EXPIRED,
        }
        filled_quantity = float(
            raw.get("filled_quantity")
            or raw.get("executed_quantity")
            or raw.get("cumulative_quantity")
            or 0
        )
        fill_price = float(
            raw.get("filled_avg_price")
            or raw.get("average_fill_price")
            or raw.get("average_price")
            or 0
        ) or None
        return BrokerOrderSnapshot(
            order_id=str(raw.get("order_id") or raw.get("id")),
            client_order_id=(
                raw.get("ref_id")
                or raw.get("client_order_id")
                or raw.get("client_id")
            ),
            symbol=str(raw.get("symbol") or ""),
            side=str(raw.get("side") or "").lower(),
            status=statuses.get(
                str(raw.get("status") or raw.get("state") or "").lower(),
                BrokerOrderStatus.UNKNOWN,
            ),
            requested_quantity=(
                float(raw.get("quantity") or raw.get("asset_quantity"))
                if raw.get("quantity") is not None
                or raw.get("asset_quantity") is not None
                else None
            ),
            filled_quantity=filled_quantity,
            filled_avg_price=fill_price,
        )

    def submit_plan(self, plan: ExecutionPlan, intent: TradeIntent) -> ExecutionResult:
        from tradingagents.execution.gateway import (
            SubmissionUncertain,
            is_uncertain_submission_error,
        )

        account = self.provider._account()
        actions = []
        keys = plan.metadata.get("leg_idempotency_keys", [])
        for index, leg in enumerate(plan.legs):
            if leg.action == PlanAction.HOLD:
                actions.append({"action": "hold", "result": {"success": True}})
                continue
            if not leg.risk_reducing and leg.side == "sell":
                return ExecutionResult(
                    success=False, decision_id=plan.decision_id, symbol=plan.symbol,
                    gateway=self.name, plan=plan, actions=actions,
                    error="Robinhood adapter does not permit opening short equity positions",
                )
            arguments = {
                "account_number": account["account_number"], "symbol": plan.symbol.upper(),
                "side": leg.side or "buy", "type": "market",
                "dollar_amount": f"{leg.notional_usd:.2f}", "market_hours": "regular_hours",
            }
            try:
                review = self.client.call_tool("review_equity_order", arguments)
            except Exception as exc:
                return ExecutionResult(
                    success=False,
                    decision_id=plan.decision_id,
                    symbol=plan.symbol,
                    gateway=self.name,
                    plan=plan,
                    actions=actions,
                    error=f"Robinhood order review failed before submission: {exc}",
                )
            result = {"success": True, "review": review, "review_only": self.review_only}
            if not self.review_only:
                if not self.live_orders_enabled:
                    return ExecutionResult(
                        success=False, decision_id=plan.decision_id, symbol=plan.symbol,
                        gateway=self.name, plan=plan, actions=actions,
                        error="Robinhood live orders require ROBINHOOD_MCP_LIVE_ORDERS_ENABLED=true",
                    )
                order_args = dict(arguments)
                order_args["ref_id"] = keys[index] if index < len(keys) else plan.decision_id
                try:
                    order = self.client.call_tool("place_equity_order", order_args)
                except Exception as exc:
                    if is_uncertain_submission_error(exc):
                        result.update(
                            {
                                "success": False,
                                "submission_uncertain": True,
                                "client_order_id": order_args["ref_id"],
                                "status": "unknown",
                                "error": str(exc),
                            }
                        )
                        actions.append({
                            "action": leg.action.value.lower(),
                            "leg": leg.model_dump(mode="json"),
                            "result": result,
                        })
                        raise SubmissionUncertain(
                            f"Robinhood submission may have been accepted: {exc}",
                            gateway=self.name,
                            leg_index=index,
                            actions=actions,
                        ) from exc
                    return ExecutionResult(
                        success=False,
                        decision_id=plan.decision_id,
                        symbol=plan.symbol,
                        gateway=self.name,
                        plan=plan,
                        actions=actions,
                        error=f"Robinhood rejected order submission: {exc}",
                    )
                result.update({"order": order, "review_only": False,
                               "order_id": order.get("order_id") if isinstance(order, dict) else None})
            actions.append({"action": leg.action.value.lower(), "leg": leg.model_dump(mode="json"),
                            "result": result})
        return ExecutionResult(
            success=True, decision_id=plan.decision_id, symbol=plan.symbol,
            gateway=self.name, plan=plan, actions=actions,
        )
