"""Models for durable portfolio allocation reservations."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel

from .batch import PortfolioDecisionBatch


class ReservationStatus(str, Enum):
    RESERVED = "reserved"
    CONSUMED = "consumed"
    RELEASED = "released"
    EXPIRED = "expired"


class AllocationReservationState(str, Enum):
    RESERVED = "reserved"
    CONSUMED = "consumed"
    RELEASED = "released"


class PortfolioReservation(BaseModel):
    reservation_id: str
    account_key: str
    status: ReservationStatus
    reserved_notional_usd: float
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    batch: PortfolioDecisionBatch
