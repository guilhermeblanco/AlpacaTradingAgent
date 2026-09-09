"""Tradier equity quote and OHLCV adapter for research tools."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from tradingagents.broker.tradier import TradierClient


def _rows(value) -> list[dict]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


class TradierMarketDataProvider:
    name = "tradier"
    TIMEFRAMES = {
        "1Min": ("1min", None),
        "5Min": ("5min", None),
        "15Min": ("15min", None),
        "1Hour": ("15min", "1h"),
        "4Hour": ("15min", "4h"),
        "1Day": ("daily", None),
    }

    def __init__(self, client: TradierClient):
        self.client = client

    def supports(self, symbol: str, timeframe: str = "1Day") -> bool:
        return "/" not in symbol and timeframe in self.TIMEFRAMES

    def get_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: Optional[str] = None,
        timeframe: str = "1Day",
    ) -> pd.DataFrame:
        if not self.supports(symbol, timeframe):
            raise ValueError(
                f"Tradier research data does not support {symbol} at {timeframe}"
            )
        interval, resample_rule = self.TIMEFRAMES[timeframe]
        if interval == "daily":
            response = self.client.request(
                "GET",
                "/markets/history",
                params={
                    "symbol": symbol,
                    "interval": "daily",
                    "start": start_date,
                    "end": end_date or datetime.now(timezone.utc).date().isoformat(),
                },
            )
            container = response.get("history") or {}
            raw_rows = _rows(container.get("day") if isinstance(container, dict) else None)
        else:
            response = self.client.request(
                "GET",
                "/markets/timesales",
                params={
                    "symbol": symbol,
                    "interval": interval,
                    "start": start_date,
                    "end": end_date or datetime.now(timezone.utc).isoformat(),
                    "session_filter": "all",
                },
            )
            container = response.get("series") or {}
            raw_rows = _rows(container.get("data") if isinstance(container, dict) else None)
        records = []
        for row in raw_rows:
            try:
                timestamp = row.get("time") or row.get("date")
                if timestamp is None and row.get("timestamp") is not None:
                    timestamp = datetime.fromtimestamp(
                        float(row["timestamp"]), tz=timezone.utc
                    )
                records.append(
                    {
                        "timestamp": pd.to_datetime(timestamp, utc=True),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row.get("volume") or 0),
                        "trade_count": row.get("trade_count"),
                        "vwap": float(row.get("vwap") or row["close"]),
                    }
                )
            except (KeyError, TypeError, ValueError, OSError):
                continue
        if not records:
            return pd.DataFrame()
        frame = (
            pd.DataFrame.from_records(records)
            .sort_values("timestamp")
            .reset_index(drop=True)
        )
        if not resample_rule:
            return frame

        indexed = frame.set_index("timestamp")
        volume = indexed["volume"].resample(resample_rule).sum()
        weighted_value = (indexed["vwap"] * indexed["volume"]).resample(
            resample_rule
        ).sum()
        aggregated = indexed.resample(resample_rule).agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
                "trade_count": "sum",
            }
        )
        aggregated["vwap"] = weighted_value.div(volume.where(volume != 0))
        aggregated["vwap"] = aggregated["vwap"].fillna(aggregated["close"])
        return aggregated.dropna(subset=["open", "close"]).reset_index()

    def get_latest_quote(self, symbol: str) -> dict:
        if not self.supports(symbol):
            raise ValueError(f"Tradier research data does not support {symbol}")
        response = self.client.request(
            "GET", "/markets/quotes", params={"symbols": symbol, "greeks": "false"}
        )
        container = response.get("quotes") or {}
        quote = container.get("quote") if isinstance(container, dict) else None
        if isinstance(quote, list):
            quote = quote[0] if quote else None
        if not isinstance(quote, dict):
            raise RuntimeError(f"Tradier returned no quote for {symbol}")
        return {
            "symbol": str(quote.get("symbol") or symbol),
            "bid_price": float(quote.get("bid") or 0),
            "ask_price": float(quote.get("ask") or 0),
            "bid_size": float(quote.get("bidsize") or 0),
            "ask_size": float(quote.get("asksize") or 0),
            "timestamp": quote.get("trade_date") or quote.get("date"),
        }
