from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tradingagents.broker.tradier import TradierClient
from tradingagents.evaluation import (
    PriceObservationUnavailable,
    TradierHistoricalPriceProvider,
)


class Transport:
    def __init__(self, *, timesales=None, history=None, timesales_error=False):
        self.timesales = timesales
        self.history = history
        self.timesales_error = timesales_error
        self.calls = []

    def __call__(self, method, path, *, params=None, data=None):
        self.calls.append((method, path, params))
        if path == "/markets/timesales":
            if self.timesales_error:
                raise RuntimeError("minute retention exceeded")
            return {"series": {"data": self.timesales}}
        if path == "/markets/history":
            return {"history": {"day": self.history}}
        raise AssertionError(path)


def _provider(transport):
    return TradierHistoricalPriceProvider(
        TradierClient(
            token="token",
            account_id="account",
            sandbox=True,
            transport=transport,
        )
    )


def _bar(start, close):
    return {"timestamp": int(start.timestamp()), "close": close}


def test_before_uses_bar_completion_and_never_future_interval() -> None:
    target = datetime(2026, 9, 8, 15, tzinfo=timezone.utc)
    transport = Transport(
        timesales=[
            _bar(target - timedelta(minutes=1), 100),
            _bar(target, 101),
        ],
        history=[],
    )

    observation = _provider(transport).price_at_or_before("MSFT", target)

    assert observation.price == 100
    assert observation.observed_at == target
    assert transport.calls[0][2]["interval"] == "1min"
    assert transport.calls[0][2]["session_filter"] == "open"


def test_after_uses_first_completed_interval_at_or_after_target() -> None:
    target = datetime(2026, 9, 8, 15, tzinfo=timezone.utc)
    transport = Transport(
        timesales=[
            _bar(target - timedelta(minutes=1), 100),
            _bar(target, 101),
        ]
    )

    observation = _provider(transport).price_at_or_after("MSFT", target)

    assert observation.price == 100
    assert observation.observed_at == target


def test_daily_fallback_treats_close_as_observation_time() -> None:
    target = datetime(2026, 9, 8, 18, tzinfo=timezone.utc)
    transport = Transport(
        timesales_error=True,
        history=[
            {"date": "2026-09-04", "close": 99},
            {"date": "2026-09-08", "close": 100},
        ],
    )
    provider = _provider(transport)

    before = provider.price_at_or_before("MSFT", target)
    after = provider.price_at_or_after("MSFT", target)

    assert before.price == 99
    assert before.observed_at == datetime(2026, 9, 4, 20, tzinfo=timezone.utc)
    assert before.observed_at <= target
    assert after.price == 100
    assert after.observed_at == datetime(2026, 9, 8, 20, tzinfo=timezone.utc)


def test_missing_or_nonpositive_prices_fail_closed() -> None:
    target = datetime(2026, 9, 8, 15, tzinfo=timezone.utc)
    with pytest.raises(PriceObservationUnavailable, match="No eligible Tradier"):
        _provider(Transport(timesales=[], history=[])).price_at_or_before(
            "MSFT", target
        )
    with pytest.raises(PriceObservationUnavailable, match="non-positive"):
        _provider(
            Transport(
                timesales=[_bar(target - timedelta(minutes=1), -1)], history=[]
            )
        ).price_at_or_before("MSFT", target)
