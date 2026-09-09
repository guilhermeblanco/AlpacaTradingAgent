"""Periodic account-wide drift detection."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import argparse
import logging
import os
import time
from typing import Callable, Optional

from pydantic import BaseModel, Field

from tradingagents.broker.models import PortfolioSnapshot


class AccountDriftReport(BaseModel):
    broker: str
    matched: bool
    checked_at: datetime
    position_differences: dict[str, float] = Field(default_factory=dict)
    cash_difference: float = 0.0
    problems: list[str] = Field(default_factory=list)
    # Positions only move when someone trades. Cash also moves on dividends,
    # interest, fees, and transfers, so the two are tracked apart: one is a
    # reason to stop trading, the other usually is not.
    positions_matched: bool = True
    cash_matched: bool = True


class AccountBaseline(BaseModel):
    broker: str
    snapshot: PortfolioSnapshot
    source: str
    source_decision_id: Optional[str] = None
    mismatch_count: int = 0
    updated_at: datetime
    checked_at: Optional[datetime] = None


def compare_account_snapshots(
    expected: PortfolioSnapshot,
    actual: PortfolioSnapshot,
    *,
    quantity_tolerance: float = 1e-6,
    cash_tolerance: float = 1.0,
) -> AccountDriftReport:
    symbols = {
        position.symbol.upper()
        for snapshot in (expected, actual)
        for position in snapshot.positions
    }
    differences = {}
    for symbol in sorted(symbols):
        expected_position = expected.position_for(symbol)
        actual_position = actual.position_for(symbol)
        difference = (
            actual_position.quantity if actual_position else 0.0
        ) - (expected_position.quantity if expected_position else 0.0)
        if abs(difference) > quantity_tolerance:
            differences[symbol] = difference
    cash_difference = actual.account.cash - expected.account.cash
    problems = [
        f"{symbol} quantity differs by {difference:g}"
        for symbol, difference in differences.items()
    ]
    cash_matched = abs(cash_difference) <= cash_tolerance
    if not cash_matched:
        problems.append(f"cash differs by {cash_difference:.2f}")
    return AccountDriftReport(
        broker=actual.broker,
        matched=not problems,
        checked_at=datetime.now(timezone.utc),
        position_differences=differences,
        cash_difference=cash_difference,
        problems=problems,
        positions_matched=not differences,
        cash_matched=cash_matched,
    )


def _default_quarantine_scope(broker: str) -> str:
    from .quarantine import execution_quarantine_scope

    return execution_quarantine_scope(broker)


@dataclass
class AccountMonitorResult:
    checked: int = 0
    bootstrapped: int = 0
    matched: int = 0
    mismatched: int = 0
    quarantined: int = 0
    resynced: int = 0
    reports: list[AccountDriftReport] = field(default_factory=list)


class AccountReconciliationMonitor:
    def __init__(
        self,
        unit_of_work_factory,
        snapshot_provider_factory: Callable[[str], object],
        brokers: list[str],
        *,
        mismatch_threshold: int = 2,
        quantity_tolerance: float = 1e-6,
        cash_tolerance: float = 1.0,
        worker_id: str = "account-monitor",
        quarantine_scope: Optional[Callable[[str], str]] = None,
        quarantine_on_cash_drift: bool = False,
    ) -> None:
        self.unit_of_work_factory = unit_of_work_factory
        self.snapshot_provider_factory = snapshot_provider_factory
        self.brokers = brokers
        self.mismatch_threshold = max(1, mismatch_threshold)
        self.quantity_tolerance = quantity_tolerance
        self.cash_tolerance = cash_tolerance
        self.worker_id = worker_id
        # Must match the scope the execution pipeline checks, otherwise the
        # quarantine pauses a service nothing enforces.
        self.quarantine_scope = quarantine_scope or _default_quarantine_scope
        self.quarantine_on_cash_drift = quarantine_on_cash_drift

    def run_once(self) -> AccountMonitorResult:
        result = AccountMonitorResult()
        for broker in self.brokers:
            actual = self.snapshot_provider_factory(broker).get_portfolio_snapshot()
            result.checked += 1
            with self.unit_of_work_factory() as uow:
                baseline = uow.account_snapshots.get(broker)
                if baseline is None:
                    uow.account_snapshots.upsert(
                        broker, actual, source="monitor_bootstrap", mismatch_count=0
                    )
                    result.bootstrapped += 1
                    uow.commit()
                    continue
                report = compare_account_snapshots(
                    baseline.snapshot,
                    actual,
                    quantity_tolerance=self.quantity_tolerance,
                    cash_tolerance=self.cash_tolerance,
                )
                result.reports.append(report)
                halting = self._is_halting(report)
                mismatch_count = baseline.mismatch_count + 1 if halting else 0
                uow.account_snapshots.record_check(
                    broker, report, mismatch_count=mismatch_count
                )
                if report.matched:
                    result.matched += 1
                else:
                    result.mismatched += 1
                if halting:
                    if mismatch_count >= self.mismatch_threshold:
                        uow.operations.set_paused(
                            self.quarantine_scope(broker),
                            paused=True,
                            reason="account-wide drift: " + "; ".join(report.problems),
                            updated_by=self.worker_id,
                        )
                        result.quarantined += 1
                elif not report.matched:
                    # Cash-only drift is expected from dividends, interest,
                    # fees, and transfers. Adopt it so the next comparison
                    # measures against reality instead of re-reporting it.
                    uow.account_snapshots.upsert(
                        broker,
                        actual,
                        source="cash_drift_resync",
                        mismatch_count=0,
                    )
                    result.resynced += 1
                uow.commit()
        return result

    def _is_halting(self, report: AccountDriftReport) -> bool:
        """Only position drift means somebody traded outside this system."""
        if not report.positions_matched:
            return True
        return self.quarantine_on_cash_drift and not report.cash_matched

    def resync_baseline(self, broker: str) -> AccountBaseline:
        """Adopt the broker's current account state as the new baseline.

        A quarantine stops execution, and only a verified fill refreshes the
        baseline, so without an explicit reset a quarantined account can
        never return to a matching state on its own.
        """
        actual = self.snapshot_provider_factory(broker).get_portfolio_snapshot()
        with self.unit_of_work_factory() as uow:
            baseline = uow.account_snapshots.upsert(
                broker, actual, source="operator_resync", mismatch_count=0
            )
            uow.commit()
        return baseline


def build_monitor_from_env():
    from tradingagents.broker import get_execution_broker_runtime
    from tradingagents.persistence import build_persistence_runtime

    config = {
        "persistence_backend": os.getenv("PERSISTENCE_BACKEND", "postgres"),
        "database_url": os.getenv("DATABASE_URL"),
        "execution_broker": os.getenv("EXECUTION_BROKER", "alpaca"),
    }
    persistence = build_persistence_runtime(config)
    if persistence.unit_of_work_factory is None:
        persistence.close()
        raise ValueError("account monitor requires PERSISTENCE_BACKEND=postgres")
    runtime = get_execution_broker_runtime(config)
    # Every process sharing an account must quarantine the same scope the
    # execution pipeline checks before it submits an order.
    from .quarantine import quarantine_scope_from_env

    # Bound once at startup so the scope cannot drift mid-process.
    scope = quarantine_scope_from_env(runtime.name)
    monitor = AccountReconciliationMonitor(
        persistence.unit_of_work_factory,
        lambda _broker: runtime.snapshot_provider,
        [runtime.name],
        mismatch_threshold=int(os.getenv("ACCOUNT_DRIFT_MISMATCH_THRESHOLD", "2")),
        cash_tolerance=float(os.getenv("ACCOUNT_DRIFT_CASH_TOLERANCE", "1")),
        quarantine_scope=lambda _broker, scope=scope: scope,
        quarantine_on_cash_drift=os.getenv(
            "ACCOUNT_DRIFT_HALT_ON_CASH", "false"
        ).strip().lower()
        in {"1", "true", "yes", "on"},
    )
    return monitor, persistence.close


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor broker account-wide drift")
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--resync-baseline",
        action="store_true",
        help=(
            "Adopt the broker's current account state as the baseline and "
            "exit. Use after reconciling a quarantine by hand."
        ),
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=float(os.getenv("ACCOUNT_MONITOR_INTERVAL_SECONDS", "60")),
    )
    args = parser.parse_args()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    monitor, close = build_monitor_from_env()
    try:
        if args.resync_baseline:
            for broker in monitor.brokers:
                baseline = monitor.resync_baseline(broker)
                logging.info(
                    "account baseline resynced broker=%s at=%s",
                    broker,
                    baseline.updated_at,
                )
            return
        while True:
            result = monitor.run_once()
            logging.info("account monitor result=%s", result)
            if args.once:
                break
            time.sleep(max(1.0, args.interval_seconds))
    finally:
        close()


if __name__ == "__main__":
    main()
