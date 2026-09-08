import pytest

from tradingagents.evaluation import (
    AlpacaHistoricalPriceProvider,
    HistoricalPriceProviderRegistry,
    default_historical_price_registry,
)


def test_default_registry_builds_alpaca_as_a_market_data_source() -> None:
    registry = default_historical_price_registry()

    assert registry.names() == ["alpaca"]
    assert isinstance(registry.create("ALPACA"), AlpacaHistoricalPriceProvider)


def test_registry_rejects_unknown_sources_instead_of_falling_back() -> None:
    registry = HistoricalPriceProviderRegistry()
    marker = object()
    registry.register("test", lambda config: marker)

    assert registry.create("test") is marker
    with pytest.raises(ValueError, match="available: test"):
        registry.create("execution-broker-name")
