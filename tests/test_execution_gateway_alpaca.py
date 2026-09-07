import unittest
from unittest.mock import patch

from tradingagents.agents.schemas import (
    ExecutableAction,
    RiskDecision,
    build_trade_intent_from_risk_decision,
)
from tradingagents.execution.alpaca_gateway import AlpacaExecutionGateway
from tradingagents.execution.models import ExecutionLeg, ExecutionPlan, PlanAction


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
        naked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
