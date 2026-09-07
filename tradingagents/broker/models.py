from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AccountSnapshot(BaseModel):
    equity: float = Field(ge=0)
    last_equity: Optional[float] = Field(default=None, ge=0)
    cash: float = 0.0
    buying_power: float = 0.0
    currency: str = "USD"


class PositionSnapshot(BaseModel):
    symbol: str
    quantity: float
    market_value: float
    average_entry_price: Optional[float] = Field(default=None, ge=0)
    current_price: Optional[float] = Field(default=None, ge=0)
    unrealized_pl: Optional[float] = None
    unrealized_intraday_pl: Optional[float] = None
    asset_class: str = "equity"

    @property
    def side(self) -> str:
        if self.quantity > 0:
            return "LONG"
        if self.quantity < 0:
            return "SHORT"
        return "NEUTRAL"


class PortfolioSnapshot(BaseModel):
    account: AccountSnapshot
    positions: list[PositionSnapshot] = Field(default_factory=list)
    captured_at: str = Field(default_factory=utc_now_iso)
    broker: str = "unknown"

    def position_for(self, symbol: str) -> Optional[PositionSnapshot]:
        key = (symbol or "").upper().replace("/", "")
        return next(
            (
                position
                for position in self.positions
                if position.symbol.upper().replace("/", "") == key
            ),
            None,
        )

    @property
    def gross_exposure(self) -> float:
        return sum(abs(position.market_value) for position in self.positions)


class QuoteSnapshot(BaseModel):
    symbol: str
    bid_price: Optional[float] = Field(default=None, gt=0)
    ask_price: Optional[float] = Field(default=None, gt=0)
    last_price: Optional[float] = Field(default=None, gt=0)
    captured_at: str = Field(default_factory=utc_now_iso)

    @property
    def reference_price(self) -> Optional[float]:
        if self.bid_price and self.ask_price:
            return (self.bid_price + self.ask_price) / 2.0
        return self.last_price or self.ask_price or self.bid_price
