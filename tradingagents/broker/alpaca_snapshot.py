from __future__ import annotations

from .models import AccountSnapshot, PortfolioSnapshot, PositionSnapshot, QuoteSnapshot


class AlpacaSnapshotProvider:
    """Strict Alpaca snapshot adapter.

    Broker failures propagate. Execution must never interpret unavailable
    account data as an empty portfolio.
    """

    def _client(self):
        from tradingagents.dataflows.alpaca_utils import get_alpaca_trading_client

        return get_alpaca_trading_client()

    def get_portfolio_snapshot(self) -> PortfolioSnapshot:
        client = self._client()
        account = client.get_account()
        positions = []
        for raw in client.get_all_positions():
            quantity = float(raw.qty)
            positions.append(
                PositionSnapshot(
                    symbol=raw.symbol,
                    quantity=quantity,
                    market_value=float(raw.market_value),
                    average_entry_price=float(raw.avg_entry_price),
                    current_price=float(raw.current_price),
                    unrealized_pl=float(raw.unrealized_pl),
                    unrealized_intraday_pl=float(raw.unrealized_intraday_pl),
                    asset_class=str(getattr(raw, "asset_class", "equity")),
                )
            )
        return PortfolioSnapshot(
            broker="alpaca",
            account=AccountSnapshot(
                equity=float(account.equity),
                last_equity=float(account.last_equity),
                cash=float(account.cash),
                buying_power=float(account.buying_power),
            ),
            positions=positions,
        )

    def get_quote_snapshot(self, symbol: str) -> QuoteSnapshot:
        from tradingagents.dataflows.alpaca_utils import AlpacaUtils

        quote = AlpacaUtils.get_latest_quote(symbol)
        return QuoteSnapshot(
            symbol=symbol,
            bid_price=quote.get("bid_price") or None,
            ask_price=quote.get("ask_price") or None,
            last_price=quote.get("last_price") or quote.get("price") or None,
        )
