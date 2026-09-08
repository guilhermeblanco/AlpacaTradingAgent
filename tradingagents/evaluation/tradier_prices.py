"""Strict point-in-time Tradier observations from minute and daily bars."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

from tradingagents.broker.tradier import TradierClient

from .attribution import PriceObservation
from .errors import PriceObservationUnavailable
from .point_in_time import ensure_aware


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


class TradierHistoricalPriceProvider:
    BAR_DURATION = timedelta(minutes=1)

    def __init__(
        self,
        client: TradierClient,
        *,
        minute_search_window: timedelta = timedelta(days=7),
        daily_search_window: timedelta = timedelta(days=45),
        equity_calendar: str = "XNYS",
    ):
        if minute_search_window <= self.BAR_DURATION:
            raise ValueError("minute search window must exceed one minute")
        if daily_search_window <= timedelta(days=1):
            raise ValueError("daily search window must exceed one day")
        import exchange_calendars

        self.client = client
        self.minute_search_window = minute_search_window
        self.daily_search_window = daily_search_window
        self.calendar = exchange_calendars.get_calendar(equity_calendar)
        self.market_timezone = ZoneInfo("America/New_York")

    def price_at_or_before(self, symbol: str, at: datetime) -> PriceObservation:
        return self._price(symbol, at=at, before=True)

    def price_at_or_after(self, symbol: str, at: datetime) -> PriceObservation:
        return self._price(symbol, at=at, before=False)

    def _price(self, symbol: str, *, at: datetime, before: bool) -> PriceObservation:
        ensure_aware(at, "at")
        minute_rows = self._minute_rows(symbol, at=at, before=before)
        selected = self._select(symbol, minute_rows, at=at, before=before)
        if selected is not None:
            return selected
        daily_rows = self._daily_rows(symbol, at=at, before=before)
        selected = self._select(symbol, daily_rows, at=at, before=before)
        if selected is not None:
            return selected
        direction = "before" if before else "after"
        raise PriceObservationUnavailable(
            f"No eligible Tradier observation for {symbol} at or {direction} "
            f"{at.isoformat()}"
        )

    def _minute_rows(
        self, symbol: str, *, at: datetime, before: bool
    ) -> list[tuple[datetime, float]]:
        start = at - self.minute_search_window if before else at - self.BAR_DURATION
        end = at if before else at + self.minute_search_window
        try:
            response = self.client.request(
                "GET",
                "/markets/timesales",
                params={
                    "symbol": symbol,
                    "interval": "1min",
                    "start": self._market_time(start),
                    "end": self._market_time(end),
                    "session_filter": "open",
                },
            )
        except RuntimeError:
            return []
        container = response.get("series") or {}
        raw_rows = _as_list(
            container.get("data") if isinstance(container, dict) else None
        )
        rows = []
        for raw in raw_rows:
            try:
                interval_start = datetime.fromtimestamp(
                    float(raw["timestamp"]), tz=timezone.utc
                )
                price = float(raw.get("close") or raw.get("price"))
            except (KeyError, TypeError, ValueError, OSError):
                continue
            rows.append((interval_start + self.BAR_DURATION, price))
        return rows

    def _daily_rows(
        self, symbol: str, *, at: datetime, before: bool
    ) -> list[tuple[datetime, float]]:
        start = at - self.daily_search_window if before else at
        end = at if before else at + self.daily_search_window
        response = self.client.request(
            "GET",
            "/markets/history",
            params={
                "symbol": symbol,
                "interval": "daily",
                "start": start.date().isoformat(),
                "end": end.date().isoformat(),
            },
        )
        container = response.get("history") or {}
        raw_rows = _as_list(
            container.get("day") if isinstance(container, dict) else None
        )
        rows = []
        for raw in raw_rows:
            try:
                session = pd.Timestamp(str(raw["date"]))
                observed_at = self.calendar.session_close(session).to_pydatetime()
                price = float(raw["close"])
            except (KeyError, TypeError, ValueError):
                continue
            rows.append((observed_at, price))
        return rows

    @staticmethod
    def _select(
        symbol: str,
        rows: list[tuple[datetime, float]],
        *,
        at: datetime,
        before: bool,
    ) -> Optional[PriceObservation]:
        target = at.astimezone(timezone.utc)
        eligible = []
        for observed_at, price in rows:
            observed = observed_at.astimezone(timezone.utc)
            if (before and observed <= target) or (not before and observed >= target):
                eligible.append((observed_at, price))
        if not eligible:
            return None
        observed_at, price = (max if before else min)(
            eligible, key=lambda row: row[0].astimezone(timezone.utc)
        )
        if price <= 0:
            raise PriceObservationUnavailable(
                f"Tradier returned a non-positive price for {symbol}"
            )
        return PriceObservation(
            symbol=symbol,
            price=price,
            observed_at=observed_at,
        )

    def _market_time(self, value: datetime) -> str:
        return value.astimezone(self.market_timezone).strftime("%Y-%m-%d %H:%M")
