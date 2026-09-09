from __future__ import annotations

import pandas as pd
import pytest

from tradingagents.marketdata import (
    AlpacaMarketDataProvider,
    MarketDataProviderRegistry,
    TradierMarketDataProvider,
    default_market_data_registry,
)
from tradingagents.broker.tradier import TradierClient


def test_registry_is_case_insensitive_and_rejects_unknown_provider() -> None:
    registry = MarketDataProviderRegistry()
    marker = object()
    registry.register("test", lambda config: marker)

    assert registry.create("TEST") is marker
    with pytest.raises(ValueError, match="available: test"):
        registry.create("missing")


def test_default_registry_builds_alpaca_provider() -> None:
    provider = default_market_data_registry().create("ALPACA")

    assert isinstance(provider, AlpacaMarketDataProvider)


def test_alpaca_provider_forwards_bar_and_quote_requests(monkeypatch) -> None:
    expected = pd.DataFrame([{"timestamp": "2026-09-01", "close": 101.0}])
    calls = []

    def get_stock_data(**kwargs):
        calls.append(kwargs)
        return expected

    monkeypatch.setattr(
        "tradingagents.dataflows.alpaca_utils.AlpacaUtils.get_stock_data",
        get_stock_data,
    )
    monkeypatch.setattr(
        "tradingagents.dataflows.alpaca_utils.AlpacaUtils.get_latest_quote",
        lambda symbol: {"symbol": symbol, "bid_price": 100.0},
    )
    provider = AlpacaMarketDataProvider()

    actual = provider.get_bars("AAPL", "2026-09-01", "2026-09-02", "1Day")

    assert actual is expected
    assert calls == [
        {
            "symbol": "AAPL",
            "start_date": "2026-09-01",
            "end_date": "2026-09-02",
            "timeframe": "1Day",
        }
    ]
    assert provider.get_latest_quote("AAPL")["symbol"] == "AAPL"


def test_tradier_provider_normalizes_daily_bars_and_quote() -> None:
    calls = []

    def transport(method, path, *, params=None, data=None):
        calls.append((method, path, params, data))
        if path == "/markets/history":
            return {
                "history": {
                    "day": [
                        {
                            "date": "2026-09-02",
                            "open": 101,
                            "high": 104,
                            "low": 100,
                            "close": 103,
                            "volume": 12345,
                        }
                    ]
                }
            }
        return {
            "quotes": {
                "quote": {
                    "symbol": "AAPL",
                    "bid": 102.9,
                    "ask": 103.1,
                    "bidsize": 10,
                    "asksize": 12,
                    "trade_date": 1788379200000,
                }
            }
        }

    provider = TradierMarketDataProvider(
        TradierClient(
            token="token", account_id="account", sandbox=True, transport=transport
        )
    )

    bars = provider.get_bars("AAPL", "2026-09-01", "2026-09-03")
    quote = provider.get_latest_quote("AAPL")

    assert list(bars.columns) == [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "trade_count",
        "vwap",
    ]
    assert bars.iloc[0]["close"] == 103.0
    assert bars.iloc[0]["vwap"] == 103.0
    assert quote["bid_price"] == 102.9
    assert calls[0][1] == "/markets/history"
    assert calls[1][1] == "/markets/quotes"


def test_tradier_provider_rejects_crypto() -> None:
    provider = TradierMarketDataProvider(
        TradierClient(
            token="token", account_id="account", transport=lambda *a, **k: {}
        )
    )

    assert not provider.supports("BTC/USD")
    with pytest.raises(ValueError, match="does not support"):
        provider.get_bars("BTC/USD", "2026-09-01")


def test_tradier_provider_builds_hourly_bars_from_fifteen_minute_data() -> None:
    requested = []

    def transport(method, path, *, params=None, data=None):
        requested.append(params)
        return {
            "series": {
                "data": [
                    {
                        "time": "2026-09-02T14:00:00Z",
                        "open": 100,
                        "high": 102,
                        "low": 99,
                        "close": 101,
                        "volume": 10,
                    },
                    {
                        "time": "2026-09-02T14:15:00Z",
                        "open": 101,
                        "high": 104,
                        "low": 100,
                        "close": 103,
                        "volume": 30,
                    },
                ]
            }
        }

    provider = TradierMarketDataProvider(
        TradierClient(
            token="token", account_id="account", sandbox=True, transport=transport
        )
    )

    bars = provider.get_bars(
        "AAPL", "2026-09-02T14:00:00Z", "2026-09-02T15:00:00Z", "1Hour"
    )

    assert requested[0]["interval"] == "15min"
    assert len(bars) == 1
    assert bars.iloc[0]["open"] == 100.0
    assert bars.iloc[0]["high"] == 104.0
    assert bars.iloc[0]["close"] == 103.0
    assert bars.iloc[0]["volume"] == 40.0
