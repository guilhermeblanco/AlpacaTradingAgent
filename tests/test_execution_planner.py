import unittest

from tradingagents.agents.schemas import (
    ExecutableAction,
    IntentType,
    RiskDecision,
    build_trade_intent_from_risk_decision,
)
from tradingagents.broker.models import (
    AccountSnapshot,
    PortfolioSnapshot,
    PositionSnapshot,
    QuoteSnapshot,
)
from tradingagents.execution.models import PlanAction
from tradingagents.execution.planner import ExecutionPlanner


def intent(action=ExecutableAction.BUY, target=8.0, current="LONG", intent_type=None):
    return build_trade_intent_from_risk_decision(
        symbol="AAPL",
        trading_mode="investment",
        current_position=current,
        decision=RiskDecision(
            action=action,
            intent_type=intent_type,
            confidence="high",
            confidence_score=0.8,
            risk_rationale="test",
            required_controls="test",
            target_portfolio_pct=target,
        ),
    )


def portfolio(market_value=3_000.0):
    positions = []
    if market_value:
        positions.append(
            PositionSnapshot(
                symbol="AAPL",
                quantity=market_value / 100.0,
                market_value=market_value,
                current_price=100.0,
            )
        )
    return PortfolioSnapshot(
        account=AccountSnapshot(equity=100_000.0),
        positions=positions,
    )


class ExecutionPlannerTests(unittest.TestCase):
    def setUp(self):
        self.planner = ExecutionPlanner()
        self.quote = QuoteSnapshot(symbol="AAPL", bid_price=99.0, ask_price=101.0)

    def test_three_percent_to_eight_percent_buys_delta(self):
        plan = self.planner.build_plan(intent(), portfolio(3_000), self.quote, 10_000)
        self.assertEqual(plan.current_allocation_pct, 3.0)
        self.assertEqual(plan.target_allocation_pct, 8.0)
        self.assertEqual(plan.delta_notional_usd, 5_000.0)
        self.assertEqual(plan.legs[0].action, PlanAction.BUY)
        self.assertEqual(plan.legs[0].notional_usd, 5_000.0)

    def test_ten_percent_to_eight_percent_reduces_only_delta(self):
        plan = self.planner.build_plan(intent(intent_type=IntentType.REDUCE), portfolio(10_000), self.quote, 10_000)
        self.assertEqual(plan.delta_notional_usd, -2_000.0)
        self.assertEqual(plan.legs[0].action, PlanAction.SELL)
        self.assertTrue(plan.legs[0].risk_reducing)

    def test_zero_target_closes_position(self):
        plan = self.planner.build_plan(
            intent(ExecutableAction.SELL, target=0.0),
            portfolio(6_000),
            self.quote,
            10_000,
        )
        self.assertEqual(plan.legs[0].action, PlanAction.CLOSE)
        self.assertEqual(plan.legs[0].notional_usd, 6_000.0)

    def test_at_target_is_hold(self):
        plan = self.planner.build_plan(intent(), portfolio(8_000), self.quote, 10_000)
        self.assertTrue(plan.is_noop)

    def test_configured_trade_amount_caps_new_exposure(self):
        plan = self.planner.build_plan(intent(), portfolio(3_000), self.quote, 1_000)
        self.assertEqual(plan.delta_notional_usd, 1_000.0)
        self.assertEqual(plan.legs[0].notional_usd, 1_000.0)

    def test_zero_trade_amount_cannot_open_exposure(self):
        plan = self.planner.build_plan(intent(), portfolio(3_000), self.quote, 0)
        self.assertTrue(plan.is_noop)

    def test_hold_never_rebalances_even_with_inconsistent_target(self):
        hold = intent(ExecutableAction.HOLD, target=8.0, current="NEUTRAL")
        plan = self.planner.build_plan(hold, portfolio(0), self.quote, 10_000)
        self.assertTrue(plan.is_noop)

    def test_legacy_intent_uses_requested_notional(self):
        legacy = intent()
        legacy.target_portfolio_pct = None
        plan = self.planner.build_plan(legacy, portfolio(0), self.quote, 1_250)
        self.assertEqual(plan.legs[0].notional_usd, 1_250)
        self.assertTrue(plan.warnings)


if __name__ == "__main__":
    unittest.main()
