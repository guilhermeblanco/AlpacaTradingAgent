"""Broker-neutral orchestration for deterministic position sizing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

from tradingagents.broker.models import PortfolioSnapshot, QuoteSnapshot
from .position_sizing import PositionSizer, RiskParameters, SizingDecision, compute_atr

if TYPE_CHECKING:
    from tradingagents.marketdata.provider import MarketDataProvider


class RiskSizingService:
    """Combine normalized snapshots with research bars for deterministic sizing."""

    def __init__(
        self,
        market_data: MarketDataProvider,
        params: Optional[RiskParameters] = None,
    ) -> None:
        self.market_data = market_data
        self.params = params or RiskParameters()
        self.position_sizer = PositionSizer(self.params)

    def __call__(
        self,
        *,
        symbol: str,
        confidence: str,
        requested_notional: float,
        portfolio: PortfolioSnapshot,
        quote: QuoteSnapshot,
        side: str = "buy",
    ) -> SizingDecision:
        price = quote.reference_price
        if price is None:
            raise ValueError(f"No reference price available for {symbol}")

        lookback_days = max(40, self.params.atr_period * 3)
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=lookback_days)
        bars = self.market_data.get_bars(
            symbol,
            start.isoformat(),
            end.isoformat(),
            timeframe="1Day",
        )
        atr = compute_atr(bars, period=self.params.atr_period)
        return self.position_sizer.size_position(
            equity=portfolio.account.equity,
            price=price,
            atr=atr,
            confidence=confidence,
            requested_notional=requested_notional,
            current_gross_exposure=portfolio.gross_exposure,
            side=side,
        )
