"""Alpaca research market-data adapter."""

from __future__ import annotations

from typing import Optional

import pandas as pd


class AlpacaMarketDataProvider:
    name = "alpaca"

    def supports(self, symbol: str, timeframe: str = "1Day") -> bool:
        return True

    def get_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: Optional[str] = None,
        timeframe: str = "1Day",
    ) -> pd.DataFrame:
        from tradingagents.dataflows.alpaca_utils import AlpacaUtils

        return AlpacaUtils.get_stock_data(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            timeframe=timeframe,
        )

    def get_latest_quote(self, symbol: str) -> dict:
        from tradingagents.dataflows.alpaca_utils import AlpacaUtils

        return AlpacaUtils.get_latest_quote(symbol)
