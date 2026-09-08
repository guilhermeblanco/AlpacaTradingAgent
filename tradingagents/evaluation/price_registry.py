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
    import os

    from .alpaca_prices import AlpacaHistoricalPriceProvider
    from .tradier_prices import TradierHistoricalPriceProvider
    from tradingagents.broker.tradier import TradierClient

    registry = HistoricalPriceProviderRegistry()
    registry.register("alpaca", lambda config: AlpacaHistoricalPriceProvider())

    def tradier(config: dict):
        token = os.getenv("TRADIER_ACCESS_TOKEN") or config.get("tradier_access_token")
        account_id = os.getenv("TRADIER_ACCOUNT_ID") or config.get("tradier_account_id")
        sandbox = str(
            os.getenv("TRADIER_USE_SANDBOX", config.get("tradier_use_sandbox", True))
        ).lower() in {"1", "true", "yes", "on"}
        if not token or not account_id:
            raise ValueError("TRADIER_ACCESS_TOKEN and TRADIER_ACCOUNT_ID are required")
        return TradierHistoricalPriceProvider(
            TradierClient(token=token, account_id=account_id, sandbox=sandbox)
        )

    registry.register("tradier", tradier)
    return registry
