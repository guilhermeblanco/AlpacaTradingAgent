from __future__ import annotations

from .snapshot import SnapshotProvider


def build_broker_prompt_context(
    provider: SnapshotProvider,
    symbol: str,
    fallback_position: str = "NEUTRAL",
) -> tuple[str, str, str]:
    """Format one broker-neutral snapshot for trader and risk prompts."""
    try:
        snapshot = provider.get_portfolio_snapshot()
    except Exception as exc:
        return (
            fallback_position,
            f"Live broker position metrics are unavailable: {exc}",
            "Live broker account metrics are unavailable.",
        )

    position = snapshot.position_for(symbol)
    current_position = position.side if position else "NEUTRAL"
    if position:
        allocation_pct = (
            position.market_value / snapshot.account.equity * 100.0
            if snapshot.account.equity
            else 0.0
        )
        position_stats = (
            f"Position Details for {symbol}:\n"
            f"- Quantity: {position.quantity:g}\n"
            f"- Average Entry Price: ${float(position.average_entry_price or 0):,.2f}\n"
            f"- Current Market Value: ${position.market_value:,.2f}\n"
            f"- Current Portfolio Allocation: {allocation_pct:.2f}%\n"
            f"- Today's P/L: ${float(position.unrealized_intraday_pl or 0):,.2f}\n"
            f"- Total P/L: ${float(position.unrealized_pl or 0):,.2f}"
        )
    else:
        position_stats = "No open position details available for this symbol."

    account = snapshot.account
    daily_change = (
        account.equity - account.last_equity
        if account.last_equity is not None
        else None
    )
    daily_pct = (
        daily_change / account.last_equity * 100.0
        if daily_change is not None and account.last_equity
        else None
    )
    account_status = (
        "Account Status:\n"
        f"- Equity: ${account.equity:,.2f}\n"
        f"- Buying Power: ${account.buying_power:,.2f}\n"
        f"- Cash: ${account.cash:,.2f}\n"
        f"- Daily Change: ${float(daily_change or 0):,.2f} ({float(daily_pct or 0):.2f}%)\n"
        f"- Gross Exposure: ${snapshot.gross_exposure:,.2f}"
    )
    return current_position, position_stats, account_status
