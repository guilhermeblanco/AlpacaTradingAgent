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
