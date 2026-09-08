from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from tradingagents.evaluation import (
    AlpacaHistoricalPriceProvider,
    PriceObservationUnavailable,
)


class Client:
    def __init__(self, frame):
        self.frame = frame
        self.requests = []

    def get_stock_bars(self, request):
        self.requests.append(request)
        return SimpleNamespace(df=self.frame)

    def get_crypto_bars(self, request):
        self.requests.append(request)
        return SimpleNamespace(df=self.frame)


def _frame(symbol="AAPL"):
    index = pd.MultiIndex.from_tuples(
        [
            (symbol, pd.Timestamp("2026-09-01T14:59:00Z")),
            (symbol, pd.Timestamp("2026-09-01T15:00:00Z")),
        ],
        names=["symbol", "timestamp"],
    )
    return pd.DataFrame({"close": [100.0, 101.0]}, index=index)


def test_selects_prices_by_when_minute_close_became_observable() -> None:
    client = Client(_frame())
    provider = AlpacaHistoricalPriceProvider(stock_client=client)
    target = datetime(2026, 9, 1, 15, 0, 30, tzinfo=timezone.utc)

    before = provider.price_at_or_before("AAPL", target)
    after = provider.price_at_or_after("AAPL", target)

    assert before.price == 100
    assert before.observed_at == datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)
    assert after.price == 101
    assert after.observed_at == datetime(2026, 9, 1, 15, 1, tzinfo=timezone.utc)
    assert client.requests[0].sort.value == "desc"
    assert client.requests[1].sort.value == "asc"


def test_routes_slash_symbols_to_crypto_without_stock_feed() -> None:
    client = Client(_frame("BTC/USD"))
    provider = AlpacaHistoricalPriceProvider(crypto_client=client)
    target = datetime(2026, 9, 1, 15, 0, 30, tzinfo=timezone.utc)

    observation = provider.price_at_or_after("BTC/USD", target)

    assert observation.price == 101
    assert len(client.requests) == 1
    assert not hasattr(client.requests[0], "feed")


def test_missing_or_ineligible_bars_fail_closed() -> None:
    target = datetime(2026, 9, 1, 15, 0, 30, tzinfo=timezone.utc)
    empty = Client(pd.DataFrame())
    provider = AlpacaHistoricalPriceProvider(stock_client=empty)

    with pytest.raises(PriceObservationUnavailable, match="No Alpaca"):
        provider.price_at_or_before("AAPL", target)

    future_only = Client(_frame().iloc[[1]])
    provider = AlpacaHistoricalPriceProvider(stock_client=future_only)
    with pytest.raises(PriceObservationUnavailable, match="No eligible"):
        provider.price_at_or_before("AAPL", target)
