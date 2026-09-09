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
        return self.baseline

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


    def test_quarantine_scope_matches_the_configured_execution_scope(self):
        """The pipeline blocks on execution_quarantine_scope, so the monitor
        must pause that exact scope rather than the bare broker name."""
        baseline = AccountBaseline(
            broker="alpaca",
            snapshot=snapshot(),
            source="verified_fill",
            mismatch_count=1,
            updated_at=datetime.now(timezone.utc),
        )
        operations = Operations()
        monitor = AccountReconciliationMonitor(
            lambda: Uow(Baselines(baseline), operations),
            lambda broker: SimpleNamespace(get_portfolio_snapshot=lambda: snapshot(8)),
            ["alpaca"],
            mismatch_threshold=2,
            quarantine_scope=lambda _broker: "execution:alpaca:paper-primary",
        )

        self.assertEqual(monitor.run_once().quarantined, 1)
        self.assertEqual(operations.paused[0][0], "execution:alpaca:paper-primary")

    def test_build_monitor_from_env_uses_the_shared_quarantine_scope(self):
        import os
        from unittest import mock

        from tradingagents.execution import account_monitor

        captured = {}

        class Runtime:
            name = "alpaca"
            snapshot_provider = SimpleNamespace(
                get_portfolio_snapshot=lambda: snapshot()
            )

        def fake_monitor(*args, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace()

        env = {
            "PERSISTENCE_BACKEND": "postgres",
            "EXECUTION_QUARANTINE_SCOPE": "execution:alpaca:paper-primary",
        }
        with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(
            account_monitor, "AccountReconciliationMonitor", fake_monitor
        ), mock.patch(
            "tradingagents.broker.get_execution_broker_runtime",
            lambda config: Runtime(),
        ), mock.patch(
            "tradingagents.persistence.build_persistence_runtime",
            lambda config: SimpleNamespace(
                unit_of_work_factory=lambda: None, close=lambda: None
            ),
        ):
            account_monitor.build_monitor_from_env()
            resolved = captured["quarantine_scope"]("alpaca")

        self.assertEqual(resolved, "execution:alpaca:paper-primary")

    def test_cash_only_drift_does_not_halt_trading(self):
        """Dividends, interest, fees, and transfers move cash without a trade."""
        baseline = AccountBaseline(
            broker="alpaca",
            snapshot=snapshot(),
            source="verified_fill",
            mismatch_count=0,
            updated_at=datetime.now(timezone.utc),
        )
        baselines = Baselines(baseline)
        operations = Operations()
        monitor = AccountReconciliationMonitor(
            lambda: Uow(baselines, operations),
            lambda broker: SimpleNamespace(
                get_portfolio_snapshot=lambda: snapshot(cash=1200)
            ),
            ["alpaca"],
            mismatch_threshold=2,
        )

        first = monitor.run_once()
        second = monitor.run_once()

        self.assertEqual(operations.paused, [])
        self.assertEqual(first.quarantined, 0)
        self.assertEqual(second.quarantined, 0)
        self.assertEqual(first.resynced, 1)
        # The adopted baseline means the next pass matches instead of
        # re-reporting the same difference forever.
        self.assertEqual(baselines.baseline.source, "cash_drift_resync")
        self.assertEqual(second.matched, 1)

    def test_cash_drift_can_still_halt_when_explicitly_requested(self):
        baseline = AccountBaseline(
            broker="alpaca",
            snapshot=snapshot(),
            source="verified_fill",
            mismatch_count=1,
            updated_at=datetime.now(timezone.utc),
        )
        operations = Operations()
        monitor = AccountReconciliationMonitor(
            lambda: Uow(Baselines(baseline), operations),
            lambda broker: SimpleNamespace(
                get_portfolio_snapshot=lambda: snapshot(cash=1200)
            ),
            ["alpaca"],
            mismatch_threshold=2,
            quarantine_on_cash_drift=True,
        )

        self.assertEqual(monitor.run_once().quarantined, 1)

    def test_position_drift_still_halts(self):
        baseline = AccountBaseline(
            broker="alpaca",
            snapshot=snapshot(),
            source="verified_fill",
            mismatch_count=1,
            updated_at=datetime.now(timezone.utc),
        )
        baselines = Baselines(baseline)
        operations = Operations()
        monitor = AccountReconciliationMonitor(
            lambda: Uow(baselines, operations),
            lambda broker: SimpleNamespace(get_portfolio_snapshot=lambda: snapshot(8)),
            ["alpaca"],
            mismatch_threshold=2,
        )

        self.assertEqual(monitor.run_once().quarantined, 1)
        # A halting drift must never be adopted as the new baseline.
        self.assertEqual(baselines.baseline.source, "verified_fill")

    def test_resync_baseline_breaks_the_quarantine_deadlock(self):
        """Only a verified fill refreshes the baseline, and a quarantine
        stops fills, so an operator needs an explicit reset."""
        baseline = AccountBaseline(
            broker="alpaca",
            snapshot=snapshot(),
            source="verified_fill",
            mismatch_count=3,
            updated_at=datetime.now(timezone.utc),
        )
        baselines = Baselines(baseline)
        monitor = AccountReconciliationMonitor(
            lambda: Uow(baselines, Operations()),
            lambda broker: SimpleNamespace(get_portfolio_snapshot=lambda: snapshot(8)),
            ["alpaca"],
        )

        resynced = monitor.resync_baseline("alpaca")

        self.assertEqual(resynced.source, "operator_resync")
        self.assertEqual(resynced.mismatch_count, 0)
        self.assertEqual(monitor.run_once().matched, 1)

    def test_report_separates_position_drift_from_cash_drift(self):
        cash_only = compare_account_snapshots(snapshot(), snapshot(cash=900))
        self.assertTrue(cash_only.positions_matched)
        self.assertFalse(cash_only.cash_matched)

        position_only = compare_account_snapshots(snapshot(), snapshot(quantity=8))
        self.assertFalse(position_only.positions_matched)
        self.assertTrue(position_only.cash_matched)

if __name__ == "__main__":
    unittest.main()
