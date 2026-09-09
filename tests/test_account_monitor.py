import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from tradingagents.broker.models import AccountSnapshot, PortfolioSnapshot, PositionSnapshot
from tradingagents.execution.account_monitor import (
    AccountBaseline,
    AccountReconciliationMonitor,
    compare_account_snapshots,
)


def snapshot(quantity=10, cash=1000):
    return PortfolioSnapshot(
        broker="tradier",
        account=AccountSnapshot(equity=10_000, cash=cash),
        positions=[PositionSnapshot(symbol="AAPL", quantity=quantity, market_value=quantity * 100)],
    )


class Baselines:
    def __init__(self, baseline):
        self.baseline = baseline

    def get(self, broker):
        return self.baseline

    def upsert(self, broker, value, **kwargs):
        self.baseline = AccountBaseline(
            broker=broker,
            snapshot=value,
            source=kwargs["source"],
            mismatch_count=kwargs.get("mismatch_count", 0),
            updated_at=datetime.now(timezone.utc),
        )

    def record_check(self, broker, report, *, mismatch_count):
        self.baseline.mismatch_count = mismatch_count


class Operations:
    def __init__(self):
        self.paused = []

    def set_paused(self, service, **kwargs):
        self.paused.append((service, kwargs))


class Uow:
    def __init__(self, baselines, operations):
        self.account_snapshots = baselines
        self.operations = operations

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def commit(self):
        pass


class AccountMonitorTests(unittest.TestCase):
    def test_compare_detects_position_and_cash_drift(self):
        report = compare_account_snapshots(snapshot(), snapshot(quantity=8, cash=900))
        self.assertFalse(report.matched)
        self.assertEqual(report.position_differences, {"AAPL": -2})
        self.assertEqual(report.cash_difference, -100)

    def test_repeated_drift_quarantines_broker(self):
        baseline = AccountBaseline(
            broker="tradier",
            snapshot=snapshot(),
            source="verified_fill",
            mismatch_count=0,
            updated_at=datetime.now(timezone.utc),
        )
        baselines = Baselines(baseline)
        operations = Operations()
        monitor = AccountReconciliationMonitor(
            lambda: Uow(baselines, operations),
            lambda broker: SimpleNamespace(get_portfolio_snapshot=lambda: snapshot(8)),
            ["tradier"],
            mismatch_threshold=2,
        )

        self.assertEqual(monitor.run_once().quarantined, 0)
        self.assertEqual(monitor.run_once().quarantined, 1)
        self.assertEqual(operations.paused[0][0], "execution:tradier")


if __name__ == "__main__":
    unittest.main()
