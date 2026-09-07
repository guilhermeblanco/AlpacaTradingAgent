from __future__ import annotations

from typing import Protocol

from .models import PortfolioSnapshot, QuoteSnapshot


class SnapshotProvider(Protocol):
    """Read-only broker boundary used by agents and execution planning."""

    def get_portfolio_snapshot(self) -> PortfolioSnapshot:
        ...

    def get_quote_snapshot(self, symbol: str) -> QuoteSnapshot:
        ...
