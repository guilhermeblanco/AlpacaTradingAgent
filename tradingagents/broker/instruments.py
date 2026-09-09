"""Normalized instrument discovery contracts."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field


class InstrumentSnapshot(BaseModel):
    symbol: str = Field(min_length=1)
    name: str = ""
    asset_class: str = "equity"
    exchange: str = ""
    tradable: bool = True
    fractionable: bool = False
    shortable: bool = False

    @property
    def asset_type(self) -> str:
        return "crypto" if self.asset_class.lower() == "crypto" else "stock"

    def to_search_result(self) -> dict:
        return {
            **self.model_dump(),
            "asset_type": self.asset_type.title(),
            "market_cap": None,
        }


class InstrumentProvider(Protocol):
    def search_instruments(
        self, query: str = "", limit: int = 12
    ) -> list[InstrumentSnapshot]: ...


class AlpacaInstrumentProvider:
    def search_instruments(self, query: str = "", limit: int = 12):
        from tradingagents.dataflows.alpaca_utils import AlpacaUtils

        rows = (
            AlpacaUtils.search_assets(query, limit=limit)
            if query
            else AlpacaUtils._get_searchable_assets()[:limit]
        )
        return [
            InstrumentSnapshot(
                symbol=row["symbol"],
                name=row.get("name") or "",
                asset_class=(
                    "crypto"
                    if str(row.get("asset_class")).lower() == "crypto"
                    else "equity"
                ),
                exchange=row.get("exchange") or "",
                tradable=bool(row.get("tradable", True)),
                fractionable=bool(row.get("fractionable", False)),
                shortable=bool(row.get("shortable", False)),
            )
            for row in rows
        ]


class TradierInstrumentProvider:
    def __init__(self, client) -> None:
        self.client = client

    def search_instruments(self, query: str = "", limit: int = 12):
        if not query.strip():
            return []
        response = self.client.request(
            "GET", "/markets/lookup", params={"q": query.strip()}
        )
        container = response.get("securities") or {}
        rows = container.get("security") if isinstance(container, dict) else []
        if not isinstance(rows, list):
            rows = [rows] if rows else []
        return [
            InstrumentSnapshot(
                symbol=str(row.get("symbol") or ""),
                name=str(row.get("description") or ""),
                asset_class="equity",
                exchange=str(row.get("exchange") or ""),
            )
            for row in rows[:limit]
            if row.get("symbol") and row.get("type") in {"stock", "etf"}
        ]


class RobinhoodInstrumentProvider:
    """Exact-symbol discovery supported by the current Robinhood MCP tools."""

    def __init__(self, client) -> None:
        self.client = client

    def search_instruments(self, query: str = "", limit: int = 12):
        symbol = query.strip().upper()
        if not symbol:
            return []
        response = self.client.call_tool("get_equity_quotes", {"symbols": [symbol]})
        rows = response.get("results") or response.get("quotes") or []
        if not isinstance(rows, list):
            rows = [rows] if rows else []
        return [
            InstrumentSnapshot(
                symbol=str(row.get("symbol") or symbol),
                name=str(row.get("name") or row.get("description") or ""),
                asset_class="equity",
                tradable=bool(row.get("tradable", True)),
                fractionable=bool(row.get("fractionable", True)),
            )
            for row in rows[:limit]
        ]
