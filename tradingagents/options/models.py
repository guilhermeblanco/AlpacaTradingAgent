from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from .occ import parse_occ_symbol


class OptionPositionIntent(str, Enum):
    BUY_TO_OPEN = "buy_to_open"
    BUY_TO_CLOSE = "buy_to_close"
    SELL_TO_OPEN = "sell_to_open"
    SELL_TO_CLOSE = "sell_to_close"


class OptionLeg(BaseModel):
    symbol: str
    ratio_quantity: int = Field(default=1, ge=1, le=100)
    position_intent: OptionPositionIntent
    bid_price: Optional[float] = Field(default=None, ge=0)
    ask_price: Optional[float] = Field(default=None, ge=0)
    open_interest: Optional[int] = Field(default=None, ge=0)
    volume: Optional[int] = Field(default=None, ge=0)

    @property
    def contract(self) -> dict:
        return parse_occ_symbol(self.symbol)


class OptionsTradeIntent(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    decision_id: str = Field(default_factory=lambda: str(uuid4()))
    underlying: str
    strategy: str
    quantity: int = Field(ge=1)
    legs: list[OptionLeg] = Field(min_length=1, max_length=4)
    order_type: Literal["limit"] = "limit"
    limit_price: float = Field(ge=0)
    max_loss_usd: float = Field(gt=0)
    thesis: str = Field(min_length=1)
    invalidation_conditions: list[str] = Field(default_factory=list)
    close_before_expiration_days: int = Field(default=1, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_underlying(self):
        self.underlying = self.underlying.upper().strip()
        return self

    @property
    def earliest_expiration(self) -> date:
        return min(leg.contract["expiration"] for leg in self.legs)


class OptionsExecutionResult(BaseModel):
    success: bool
    decision_id: str
    underlying: str
    gateway: str
    intent: OptionsTradeIntent
    broker_order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    status: Optional[str] = None
    validations: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    raw: dict[str, Any] = Field(default_factory=dict)
