import unittest
from unittest.mock import MagicMock, patch

from tradingagents.agents.schemas import (
    ExecutableAction,
    RiskDecision,
    build_trade_intent_from_risk_decision,
)
from tradingagents.execution.alpaca_gateway import AlpacaExecutionGateway
from tradingagents.execution.gateway import SubmissionUncertain
from tradingagents.execution.models import ExecutionLeg, ExecutionPlan, PlanAction
from tradingagents.dataflows.alpaca_utils import AlpacaUtils


def protected_intent():
    return build_trade_intent_from_risk_decision(
        symbol="AAPL",
        trading_mode="investment",
        current_position="NEUTRAL",
        decision=RiskDecision(
            action=ExecutableAction.BUY,
            confidence="high",
            risk_rationale="test",
            required_controls="test",
            stop_loss="95",
            take_profit="110",
        ),
    )


def plan(intent):
    return ExecutionPlan(
        decision_id=intent.decision_id,
        symbol="AAPL",
        intent_schema_version="2.0",
        current_allocation_pct=0,
        current_notional_usd=0,
        delta_notional_usd=5_000,
        reference_price=100,
        metadata={"leg_idempotency_keys": ["ata-test-0"]},
        legs=[
            ExecutionLeg(
                action=PlanAction.BUY,
                side="buy",
                notional_usd=5_000,
                quantity=50,
                reason="test",
            )
        ],
    )


class AlpacaGatewayTests(unittest.TestCase):
    def test_alpaca_helper_preserves_transport_uncertainty(self):
        client = MagicMock()
        client.submit_order.side_effect = TimeoutError("response timed out")
        with patch(
            "tradingagents.dataflows.alpaca_utils.get_alpaca_trading_client",
            return_value=client,
        ):
            result = AlpacaUtils.place_market_order(
                "AAPL", "buy", qty=1, client_order_id="stable-key"
            )
        self.assertFalse(result["success"])
        self.assertTrue(result["submission_uncertain"])
        self.assertEqual(result["error_type"], "TimeoutError")

    def test_protective_order_rejection_does_not_submit_naked_fallback(self):
        intent = protected_intent()
        with patch(
            "tradingagents.dataflows.alpaca_utils.AlpacaUtils.place_protected_market_order",
            return_value={"success": False, "error": "bracket rejected"},
        ) as protected, patch(
            "tradingagents.dataflows.alpaca_utils.AlpacaUtils.place_market_order"
        ) as naked:
            result = AlpacaExecutionGateway().submit_plan(plan(intent), intent)

        self.assertFalse(result.success)
        self.assertIn("bracket rejected", result.error)
        protected.assert_called_once()
        self.assertEqual(protected.call_args.kwargs["client_order_id"], "ata-test-0")
        naked.assert_not_called()

    def test_transport_timeout_is_reported_as_uncertain(self):
        intent = protected_intent()
        with patch(
            "tradingagents.dataflows.alpaca_utils.AlpacaUtils.place_protected_market_order",
            return_value={
                "success": False,
                "error": "request timed out",
                "submission_uncertain": True,
                "client_order_id": "ata-test-0",
            },
        ):
            with self.assertRaises(SubmissionUncertain) as raised:
                AlpacaExecutionGateway().submit_plan(plan(intent), intent)
        self.assertEqual(raised.exception.leg_index, 0)
        self.assertEqual(
            raised.exception.actions[0]["result"]["client_order_id"],
            "ata-test-0",
        )

    def test_close_uses_idempotent_quantity_order(self):
        intent = protected_intent()
        close_plan = plan(intent)
        close_plan.legs[0].action = PlanAction.CLOSE
        close_plan.legs[0].side = "sell"
        close_plan.legs[0].risk_reducing = True
        with patch(
            "tradingagents.dataflows.alpaca_utils.AlpacaUtils.place_market_order",
            return_value={"success": True, "order_id": "close-1"},
        ) as market, patch(
            "tradingagents.dataflows.alpaca_utils.AlpacaUtils.close_position"
        ) as close:
            result = AlpacaExecutionGateway().submit_plan(close_plan, intent)
        self.assertTrue(result.success)
        market.assert_called_once_with(
            "AAPL",
            "sell",
            notional=None,
            qty=50.0,
            client_order_id="ata-test-0",
        )
        close.assert_not_called()


if __name__ == "__main__":
    unittest.main()


def _order(order_id="o1", *, status="filled", qty="50", filled_qty="50",
           filled_avg_price="100.0", legs=None, client_order_id="ata-test-0"):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=order_id,
        client_order_id=client_order_id,
        symbol="AAPL",
        side=SimpleNamespace(value="buy"),
        status=SimpleNamespace(value=status),
        qty=qty,
        filled_qty=filled_qty,
        filled_avg_price=filled_avg_price,
        legs=legs,
    )


class OrderSnapshotTests(unittest.TestCase):
    """Reconciliation reads broker state through this; a status it cannot
    map has to become UNKNOWN rather than raising, or a run that already
    placed an order can never resolve it."""

    def _snapshot(self, order, *, by_id=True, **kwargs):
        client = MagicMock()
        client.get_order_by_id.return_value = order
        client.get_order_by_client_id.return_value = order
        with patch(
            "tradingagents.dataflows.alpaca_utils.get_alpaca_trading_client",
            return_value=client,
        ):
            snapshot = AlpacaExecutionGateway().get_order_snapshot(**kwargs)
        return snapshot, client

    def test_an_order_is_normalized_by_id(self):
        snapshot, client = self._snapshot(_order(), order_id="o1")

        self.assertEqual(snapshot.order_id, "o1")
        self.assertEqual(snapshot.symbol, "AAPL")
        self.assertEqual(snapshot.side, "buy")
        self.assertEqual(snapshot.requested_quantity, 50.0)
        self.assertEqual(snapshot.filled_quantity, 50.0)
        self.assertEqual(snapshot.filled_avg_price, 100.0)
        client.get_order_by_id.assert_called_once()

    def test_a_client_order_id_is_resolved_to_the_broker_order(self):
        """The client id is what a retry knows; the broker id is what the
        nested lookup needs."""
        snapshot, client = self._snapshot(_order(), client_order_id="ata-test-0")

        client.get_order_by_client_id.assert_called_once_with("ata-test-0")
        self.assertEqual(snapshot.client_order_id, "ata-test-0")

    def test_asking_for_neither_is_refused(self):
        with self.assertRaises(ValueError):
            AlpacaExecutionGateway().get_order_snapshot()

    def test_an_unmapped_status_reads_as_unknown(self):
        from tradingagents.execution.reconciliation import BrokerOrderStatus

        snapshot, _client = self._snapshot(
            _order(status="held_for_review"), order_id="o1"
        )

        self.assertEqual(snapshot.status, BrokerOrderStatus.UNKNOWN)

    def test_an_unfilled_order_reads_as_zero_filled(self):
        snapshot, _client = self._snapshot(
            _order(status="new", filled_qty=None, filled_avg_price=None), order_id="o1"
        )

        self.assertEqual(snapshot.filled_quantity, 0.0)
        self.assertIsNone(snapshot.filled_avg_price)

    def test_a_notional_order_has_no_requested_quantity(self):
        snapshot, _client = self._snapshot(_order(qty=None), order_id="o1")

        self.assertIsNone(snapshot.requested_quantity)

    def test_bracket_legs_are_carried_through(self):
        """The stop and target are what make an entry protected."""
        snapshot, _client = self._snapshot(
            _order(
                legs=[
                    _order("stop", status="new", filled_qty="0", filled_avg_price=None),
                    _order("target", status="new", filled_qty="0", filled_avg_price=None),
                ]
            ),
            order_id="o1",
        )

        self.assertEqual(
            [child.order_id for child in snapshot.child_orders], ["stop", "target"]
        )

    def test_an_order_with_no_legs_has_no_children(self):
        snapshot, _client = self._snapshot(_order(legs=None), order_id="o1")

        self.assertEqual(snapshot.child_orders, [])


class ClosePositionTests(unittest.TestCase):
    def test_closing_delegates_to_the_broker_helper(self):
        with patch.object(
            AlpacaUtils, "close_position", return_value={"success": True}
        ) as close:
            result = AlpacaExecutionGateway().close_position("NVDA")

        close.assert_called_once_with("NVDA")
        self.assertTrue(result["success"])


class HoldLegTests(unittest.TestCase):
    def test_a_hold_leg_sends_nothing_but_still_reports(self):
        intent = protected_intent()
        held = plan(intent)
        held.legs = [
            ExecutionLeg(action=PlanAction.HOLD, reason="already at target weight")
        ]

        with patch.object(AlpacaUtils, "place_market_order") as place:
            result = AlpacaExecutionGateway().submit_plan(held, intent)

        place.assert_not_called()
        self.assertEqual(result.actions[0]["action"], "hold")
        self.assertIn("target weight", result.actions[0]["result"]["message"])
