"""Read-only broker adapter preflight and certification checks."""

from __future__ import annotations

import argparse
import json
import os
from enum import Enum

from pydantic import BaseModel, Field

from .registry import BrokerRuntime, get_execution_broker_runtime


class CheckStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class PreflightCheck(BaseModel):
    name: str
    status: CheckStatus
    message: str
    details: dict = Field(default_factory=dict)


class BrokerPreflightReport(BaseModel):
    broker: str
    symbol: str
    checks: list[PreflightCheck]

    @property
    def ready(self) -> bool:
        return all(check.status != CheckStatus.FAIL for check in self.checks)


def certify_broker_runtime(
    runtime: BrokerRuntime,
    *,
    symbol: str = "AAPL",
    require_paper: bool = True,
) -> BrokerPreflightReport:
    checks = [
        PreflightCheck(
            name="capabilities",
            status=CheckStatus.PASS,
            message="Broker capability contract loaded.",
            details=runtime.capabilities.to_dict(),
        )
    ]
    if require_paper and not runtime.capabilities.paper_trading:
        checks.append(
            PreflightCheck(
                name="safety_mode",
                status=CheckStatus.FAIL,
                message="Paper or sandbox trading is required for certification.",
            )
        )
    else:
        checks.append(
            PreflightCheck(
                name="safety_mode",
                status=CheckStatus.PASS,
                message=(
                    "Paper or sandbox mode is active."
                    if runtime.capabilities.paper_trading
                    else "Live-mode preflight was explicitly allowed."
                ),
            )
        )

    try:
        portfolio = runtime.snapshot_provider.get_portfolio_snapshot()
        checks.append(
            PreflightCheck(
                name="account_snapshot",
                status=CheckStatus.PASS,
                message="Account and positions normalized successfully.",
                details={
                    "currency": portfolio.account.currency,
                    "positions": len(portfolio.positions),
                    "equity_positive": portfolio.account.equity > 0,
                },
            )
        )
    except Exception as exc:
        checks.append(
            PreflightCheck(
                name="account_snapshot",
                status=CheckStatus.FAIL,
                message=f"Account snapshot failed: {exc}",
            )
        )

    try:
        quote = runtime.snapshot_provider.get_quote_snapshot(symbol)
        if quote.reference_price is None:
            raise ValueError("quote did not include a positive reference price")
        checks.append(
            PreflightCheck(
                name="market_quote",
                status=CheckStatus.PASS,
                message=f"Quote normalized for {symbol}.",
                details={"reference_price": quote.reference_price},
            )
        )
    except Exception as exc:
        checks.append(
            PreflightCheck(
                name="market_quote",
                status=CheckStatus.FAIL,
                message=f"Quote check failed: {exc}",
            )
        )

    provider = runtime.instrument_provider
    if provider is None:
        checks.append(
            PreflightCheck(
                name="instrument_lookup",
                status=CheckStatus.WARN,
                message="Adapter does not expose instrument discovery.",
            )
        )
    else:
        try:
            matches = provider.search_instruments(symbol, limit=5)
            exact = next((item for item in matches if item.symbol == symbol), None)
            if exact is None or not exact.tradable:
                raise ValueError(f"{symbol} was not returned as tradable")
            checks.append(
                PreflightCheck(
                    name="instrument_lookup",
                    status=CheckStatus.PASS,
                    message=f"{symbol} is available for trading.",
                    details=exact.model_dump(mode="json"),
                )
            )
        except Exception as exc:
            checks.append(
                PreflightCheck(
                    name="instrument_lookup",
                    status=CheckStatus.FAIL,
                    message=f"Instrument lookup failed: {exc}",
                )
            )

    for name, method in (
        ("order_reconciliation", "get_order_snapshot"),
        ("risk_reducing_close", "close_position"),
    ):
        available = callable(getattr(runtime.execution_gateway, method, None))
        checks.append(
            PreflightCheck(
                name=name,
                status=CheckStatus.PASS if available else CheckStatus.FAIL,
                message=(
                    f"Gateway implements {method}."
                    if available
                    else f"Gateway is missing {method}."
                ),
            )
        )
    return BrokerPreflightReport(broker=runtime.name, symbol=symbol, checks=checks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run read-only broker preflight")
    parser.add_argument("--broker", default=os.getenv("EXECUTION_BROKER", "alpaca"))
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--allow-live", action="store_true")
    args = parser.parse_args()
    config = {"execution_broker": args.broker}
    report = certify_broker_runtime(
        get_execution_broker_runtime(config),
        symbol=args.symbol.upper(),
        require_paper=not args.allow_live,
    )
    print(json.dumps(report.model_dump(mode="json"), indent=2))
    if not report.ready:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
