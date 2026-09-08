"""Research quote and bar contracts independent of execution brokers."""

from __future__ import annotations

from typing import Callable, Optional, Protocol

import pandas as pd


class MarketDataProvider(Protocol):
    name: str

    def supports(self, symbol: str, timeframe: str = "1Day") -> bool: ...

    def get_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: Optional[str] = None,
        timeframe: str = "1Day",
    ) -> pd.DataFrame: ...

    def get_latest_quote(self, symbol: str) -> dict: ...


class MarketDataProviderRegistry:
    def __init__(self):
        self._factories: dict[str, Callable[[dict], MarketDataProvider]] = {}

    def register(
        self, name: str, factory: Callable[[dict], MarketDataProvider]
    ) -> None:
        key = name.lower().strip()
        if not key:
            raise ValueError("market-data provider name is required")
        self._factories[key] = factory

    def create(self, name: str, config: Optional[dict] = None) -> MarketDataProvider:
        key = name.lower().strip()
        if key not in self._factories:
            raise ValueError(
                f"Unknown research market-data provider {name!r}; "
                f"available: {', '.join(self.names())}"
            )
        return self._factories[key](config or {})

    def names(self) -> list[str]:
        return sorted(self._factories)
