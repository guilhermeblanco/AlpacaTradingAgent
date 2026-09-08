"""Configuration-driven research market-data registry."""

from __future__ import annotations

import os

from .provider import MarketDataProviderRegistry


def default_market_data_registry() -> MarketDataProviderRegistry:
    from tradingagents.dataflows.config import get_api_key

    from .alpaca import AlpacaMarketDataProvider
    from .tradier import TradierMarketDataProvider
    from tradingagents.broker.tradier import TradierClient

    registry = MarketDataProviderRegistry()
    registry.register("alpaca", lambda config: AlpacaMarketDataProvider())

    def tradier(config: dict):
        token = get_api_key("tradier_access_token", "TRADIER_ACCESS_TOKEN")
        account_id = get_api_key("tradier_account_id", "TRADIER_ACCOUNT_ID")
        sandbox = str(
            config.get("tradier_use_sandbox", os.getenv("TRADIER_USE_SANDBOX", "true"))
        ).lower() in {"1", "true", "yes", "on"}
        if not token or not account_id:
            raise ValueError("Tradier credentials are required for research market data")
        return TradierMarketDataProvider(
            TradierClient(
                token=token,
                account_id=account_id,
                sandbox=sandbox,
            )
        )

    registry.register("tradier", tradier)
    return registry


def get_research_market_data_provider(config: dict | None = None):
    if config is None:
        from tradingagents.dataflows.config import get_config

        config = get_config() or {}
    name = str(
        config.get("research_market_data_provider")
        or os.getenv("RESEARCH_MARKET_DATA_PROVIDER")
        or "alpaca"
    )
    return default_market_data_registry().create(name, config)
