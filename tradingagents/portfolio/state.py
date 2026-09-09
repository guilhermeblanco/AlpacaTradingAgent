"""Broker-neutral portfolio state collection."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, Optional, Tuple

import pandas as pd

from tradingagents.broker.snapshot import SnapshotProvider
from tradingagents.marketdata.provider import MarketDataProvider


def gather_portfolio_state(
    symbol: str,
    snapshot_provider: SnapshotProvider,
    market_data: MarketDataProvider,
    lookback_days: int = 120,
) -> Tuple[Optional[float], Dict[str, float], Dict[str, pd.DataFrame]]:
    """Collect normalized equity, positions, and research price histories."""
    snapshot = snapshot_provider.get_portfolio_snapshot()
    open_positions = {
        position.symbol.upper(): abs(position.market_value)
        for position in snapshot.positions
        if position.quantity != 0
    }
    start = (date.today() - timedelta(days=lookback_days)).isoformat()
    price_history: Dict[str, pd.DataFrame] = {}
    requested = symbol.upper()
    for wanted in {requested, *open_positions}:
        if not market_data.supports(wanted, "1Day"):
            continue
        try:
            price_history[wanted] = market_data.get_bars(
                wanted, start, None, timeframe="1Day"
            )
        except Exception:
            continue
    if requested in price_history and symbol not in price_history:
        price_history[symbol] = price_history[requested]
    return snapshot.account.equity, open_positions, price_history
