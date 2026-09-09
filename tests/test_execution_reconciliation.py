import unittest

from tradingagents.broker.models import AccountSnapshot, PortfolioSnapshot, PositionSnapshot
from tradingagents.execution.account_reconciliation import reconcile_account_position
from tradingagents.execution.models import ExecutionLeg, ExecutionPlan, PlanAction
from tradingagents.execution.reconciliation import (
    BrokerOrderSnapshot, BrokerOrderStatus, ExecutionReconciler,
)


def plan(quantity=10):
    return ExecutionPlan(
        decision_id="d1", symbol="AAPL", intent_schema_version="2.0",
        current_allocation_pct=0, current_notional_usd=0, delta_notional_usd=1000,
        legs=[ExecutionLeg(action=PlanAction.BUY, side="buy", notional_usd=1000,
                           quantity=quantity, reason="test")],
        metadata={"leg_idempotency_keys": ["ata-d1-0"]},
    )


class FakeGateway:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.lookup = None

    def get_order_snapshot(self, *, order_id=None, client_order_id=None):
        self.lookup = (order_id, client_order_id)
        return self.snapshot


class ExecutionReconciliationTests(unittest.TestCase):
    def test_complete_fill_and_matching_protection_reconciles(self):
        child = BrokerOrderSnapshot(order_id="child", symbol="AAPL", side="sell",
                                    status=BrokerOrderStatus.NEW, requested_quantity=10)
        gateway = FakeGateway(BrokerOrderSnapshot(
            order_id="parent", symbol="AAPL", side="buy", status=BrokerOrderStatus.FILLED,
            requested_quantity=10, filled_quantity=10, filled_avg_price=100,
            child_orders=[child],
        ))
        report = ExecutionReconciler(gateway).reconcile(plan(), [{"result": {"order_id": "parent"}}])
        self.assertTrue(report.complete)
        self.assertEqual(gateway.lookup, ("parent", "ata-d1-0"))

    def test_partial_fill_is_not_complete(self):
        gateway = FakeGateway(BrokerOrderSnapshot(
            order_id="parent", symbol="AAPL", side="buy",
            status=BrokerOrderStatus.PARTIALLY_FILLED, requested_quantity=10, filled_quantity=4,
        ))
        report = ExecutionReconciler(gateway).reconcile(plan(), [{"result": {"order_id": "parent"}}])
        self.assertFalse(report.complete)

    def test_mismatched_child_quantity_is_reported(self):
        child = BrokerOrderSnapshot(order_id="child", symbol="AAPL", side="sell",
                                    status=BrokerOrderStatus.NEW, requested_quantity=10)
        gateway = FakeGateway(BrokerOrderSnapshot(
            order_id="parent", symbol="AAPL", side="buy", status=BrokerOrderStatus.FILLED,
            requested_quantity=10, filled_quantity=4, child_orders=[child],
        ))
        report = ExecutionReconciler(gateway).reconcile(plan(quantity=4), [{"result": {"order_id": "parent"}}])
        self.assertFalse(report.complete)
        self.assertIn("protective child quantity", report.legs[0].problems[0])

    def test_account_position_matches_pre_trade_quantity_plus_fills(self):
        execution_plan = plan(quantity=4)
        execution_plan.metadata["pre_trade_quantity"] = 6
        order_report = ExecutionReconciler(
            FakeGateway(
                BrokerOrderSnapshot(
                    order_id="parent",
                    symbol="AAPL",
                    side="buy",
                    status=BrokerOrderStatus.FILLED,
                    requested_quantity=4,
                    filled_quantity=4,
                    filled_avg_price=100,
                )
            )
        ).reconcile(execution_plan, [{"result": {"order_id": "parent"}}])
        account_report = reconcile_account_position(
            execution_plan,
            order_report,
            PortfolioSnapshot(
                broker="alpaca",
                account=AccountSnapshot(equity=1_000),
                positions=[
                    PositionSnapshot(symbol="AAPL", quantity=10, market_value=1_000)
                ],
            ),
        )

        self.assertTrue(account_report.checked)
        self.assertTrue(account_report.matched)
        self.assertEqual(account_report.expected_quantity, 10)

    def test_account_position_reports_external_drift(self):
        execution_plan = plan(quantity=4)
        execution_plan.metadata["pre_trade_quantity"] = 0
        order_report = ExecutionReconciler(
            FakeGateway(
                BrokerOrderSnapshot(
                    order_id="parent",
                    symbol="AAPL",
                    side="buy",
                    status=BrokerOrderStatus.FILLED,
                    filled_quantity=4,
                    filled_avg_price=100,
                )
            )
        ).reconcile(execution_plan, [{"result": {"order_id": "parent"}}])
        account_report = reconcile_account_position(
            execution_plan,
            order_report,
            PortfolioSnapshot(
                broker="tradier",
                account=AccountSnapshot(equity=1_000),
                positions=[
                    PositionSnapshot(symbol="AAPL", quantity=3, market_value=300)
                ],
            ),
        )

        self.assertFalse(account_report.matched)
        self.assertEqual(account_report.difference, -1)


if __name__ == "__main__":
    unittest.main()
