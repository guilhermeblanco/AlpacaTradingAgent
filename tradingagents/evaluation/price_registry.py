"""Registry for historical market-data providers, independent of brokers."""

from __future__ import annotations

from typing import Callable

from .attribution import HistoricalPriceProvider


class HistoricalPriceProviderRegistry:
    def __init__(self):
        self._factories: dict[str, Callable[[dict], HistoricalPriceProvider]] = {}

    def register(
        self,
        name: str,
        factory: Callable[[dict], HistoricalPriceProvider],
    ) -> None:
        key = name.lower().strip()
        if not key:
            raise ValueError("historical price provider name is required")
        self._factories[key] = factory

    def create(
        self, name: str, config: dict | None = None
    ) -> HistoricalPriceProvider:
        key = name.lower().strip()
        if key not in self._factories:
            raise ValueError(
                f"Unknown historical price provider {name!r}; "
                f"available: {', '.join(self.names())}"
            )
        return self._factories[key](config or {})

    def names(self) -> list[str]:
        return sorted(self._factories)


def default_historical_price_registry() -> HistoricalPriceProviderRegistry:
    from .alpaca_prices import AlpacaHistoricalPriceProvider

    registry = HistoricalPriceProviderRegistry()
    registry.register("alpaca", lambda config: AlpacaHistoricalPriceProvider())
    return registry
