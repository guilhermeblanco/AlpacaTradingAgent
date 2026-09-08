"""Strict point-in-time price observations backed by Alpaca minute bars."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pandas as pd

from .attribution import PriceObservation
from .errors import PriceObservationUnavailable
from .point_in_time import ensure_aware


class AlpacaHistoricalPriceProvider:
    BAR_DURATION = timedelta(minutes=1)

    def __init__(
        self,
        *,
        stock_client: Optional[Any] = None,
        crypto_client: Optional[Any] = None,
        search_window: timedelta = timedelta(days=14),
    ):
        if search_window <= self.BAR_DURATION:
            raise ValueError("price search window must exceed one minute")
        self.stock_client = stock_client
        self.crypto_client = crypto_client
        self.search_window = search_window

    def price_at_or_before(self, symbol: str, at: datetime) -> PriceObservation:
        ensure_aware(at, "at")
        frame = self._fetch(
            symbol,
            start=at - self.search_window,
            end=at,
            ascending=False,
        )
        return self._select(symbol, frame, at=at, before=True)

    def price_at_or_after(self, symbol: str, at: datetime) -> PriceObservation:
        ensure_aware(at, "at")
        frame = self._fetch(
            symbol,
            start=at - self.BAR_DURATION,
            end=at + self.search_window,
            ascending=True,
        )
        return self._select(symbol, frame, at=at, before=False)

    def _fetch(
        self,
        symbol: str,
        *,
        start: datetime,
        end: datetime,
        ascending: bool,
    ) -> pd.DataFrame:
        from alpaca.common.enums import Sort
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        sort = Sort.ASC if ascending else Sort.DESC
        if "/" in symbol:
            client = self.crypto_client or self._default_crypto_client()
            request = CryptoBarsRequest(
                symbol_or_symbols=[symbol],
                timeframe=TimeFrame.Minute,
                start=start,
                end=end,
                limit=10,
                sort=sort,
            )
            response = client.get_crypto_bars(request)
        else:
            client = self.stock_client or self._default_stock_client()
            request = StockBarsRequest(
                symbol_or_symbols=[symbol],
                timeframe=TimeFrame.Minute,
                start=start,
                end=end,
                limit=10,
                sort=sort,
                feed=DataFeed.IEX,
            )
            response = client.get_stock_bars(request)
        frame = response.df.reset_index()
        if "symbol" in frame.columns:
            frame = frame[frame["symbol"] == symbol]
        return frame

    def _select(
        self,
        symbol: str,
        frame: pd.DataFrame,
        *,
        at: datetime,
        before: bool,
    ) -> PriceObservation:
        required = {"timestamp", "close"}
        if frame is None or frame.empty or not required.issubset(frame.columns):
            raise PriceObservationUnavailable(
                f"No Alpaca minute-bar observation for {symbol} near {at.isoformat()}"
            )
        rows = frame.loc[:, ["timestamp", "close"]].copy()
        rows["timestamp"] = pd.to_datetime(rows["timestamp"], utc=True)
        rows["observed_at"] = rows["timestamp"] + self.BAR_DURATION
        target = pd.Timestamp(at).tz_convert("UTC")
        if before:
            eligible = rows[rows["observed_at"] <= target].sort_values(
                "observed_at", ascending=False
            )
        else:
            eligible = rows[rows["observed_at"] >= target].sort_values(
                "observed_at", ascending=True
            )
        if eligible.empty:
            raise PriceObservationUnavailable(
                f"No eligible Alpaca observation for {symbol} near {at.isoformat()}"
            )
        row = eligible.iloc[0]
        price = float(row["close"])
        if price <= 0:
            raise PriceObservationUnavailable(
                f"Alpaca returned a non-positive close for {symbol}"
            )
        observed_at = row["observed_at"].to_pydatetime()
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        return PriceObservation(
            symbol=symbol,
            price=price,
            observed_at=observed_at,
        )

    @staticmethod
    def _default_stock_client():
        from tradingagents.dataflows.alpaca_utils import get_alpaca_stock_client

        return get_alpaca_stock_client()

    @staticmethod
    def _default_crypto_client():
        from tradingagents.dataflows.alpaca_utils import get_alpaca_crypto_client

        return get_alpaca_crypto_client()
