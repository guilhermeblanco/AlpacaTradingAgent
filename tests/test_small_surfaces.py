"""Tests for small modules that nothing else exercises.

Each of these is a single decision point — a registry lookup, a validator, a
gateway shim — that a larger test happens to route around. They are cheap to
get wrong and cheap to pin.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from tradingagents.evaluation import point_in_time, promotion
from tradingagents.evaluation.models import EvaluationOutcome
from tradingagents.execution.dry_run_gateway import DryRunExecutionGateway
from tradingagents.llm_clients import validators
from tradingagents.marketdata import provider as marketdata_provider


class ValidatorTests(unittest.TestCase):
    def test_a_catalogued_model_validates(self):
        from tradingagents.openai_model_registry import get_model_options_for_provider

        known = get_model_options_for_provider("openai", "quick")[0]["value"]

        self.assertTrue(validators.validate_model("openai", known))

    def test_a_custom_model_id_is_allowed(self):
        """Every provider accepts an id outside the curated catalog."""
        self.assertIsInstance(
            validators.validate_model("openrouter", "some-org/some-model"), bool
        )

    def test_an_unknown_provider_does_not_raise(self):
        self.assertIsInstance(validators.validate_model("made-up", "x"), bool)


class DryRunGatewayTests(unittest.TestCase):
    """Dry run journals a plan without sending anything to a broker."""

    def test_the_gateway_reports_its_name(self):
        self.assertIsInstance(DryRunExecutionGateway().name, str)

    def test_closing_a_position_sends_nothing(self):
        gateway = DryRunExecutionGateway()

        result = gateway.close_position("NVDA")

        self.assertIsInstance(result, dict)

    def test_submitting_a_plan_returns_a_result_without_a_broker(self):
        from tradingagents.agents.schemas import (
            ExecutableAction,
            RiskDecision,
            build_trade_intent_from_risk_decision,
        )
        from tradingagents.execution.planner import ExecutionPlanner
        from tradingagents.broker.models import (
            AccountSnapshot,
            PortfolioSnapshot,
            QuoteSnapshot,
        )

        intent = build_trade_intent_from_risk_decision(
            symbol="NVDA",
            trading_mode="investment",
            current_position="NEUTRAL",
            decision=RiskDecision(
                action=ExecutableAction.BUY,
                confidence="high",
                risk_rationale="test",
                required_controls="stop at 95",
                target_portfolio_pct=1.0,
            ),
            trade_date="2026-09-09",
        )
        plan = ExecutionPlanner().build_plan(
            intent,
            PortfolioSnapshot(account=AccountSnapshot(equity=100_000.0)),
            QuoteSnapshot(symbol="NVDA", bid_price=99.0, ask_price=101.0),
            1_000.0,
        )

        result = DryRunExecutionGateway().submit_plan(plan, intent)

        self.assertIsNotNone(result)


class MarketDataProviderTests(unittest.TestCase):
    def test_the_protocol_declares_the_methods_adapters_implement(self):
        for method in ("get_bars", "supports"):
            self.assertTrue(
                hasattr(marketdata_provider.MarketDataProvider, method), method
            )

    def test_an_adapter_satisfying_the_protocol_is_recognized(self):
        adapter = SimpleNamespace(
            name="fake",
            get_bars=lambda **k: None,
            supports=lambda symbol: True,
        )

        self.assertTrue(hasattr(adapter, "get_bars"))
        self.assertTrue(adapter.supports("NVDA"))


class PointInTimeTests(unittest.TestCase):
    def test_a_violation_carries_its_message(self):
        error = point_in_time.PointInTimeViolation("data is from the future")

        self.assertIn("future", str(error))

    def test_an_episode_using_only_past_data_is_accepted(self):
        from datetime import datetime, timedelta, timezone

        from tradingagents.evaluation.models import EvaluationEpisode

        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        episode = EvaluationEpisode(
            decision_id="d1",
            symbol="NVDA",
            action="BUY",
            decision_at=now,
            data_as_of=now - timedelta(minutes=5),
            reference_price=100.0,
            benchmark_price=500.0,
        )

        point_in_time.validate_episode_point_in_time(episode)

    def test_an_episode_using_future_data_is_refused(self):
        from datetime import datetime, timedelta, timezone

        from tradingagents.evaluation.models import EvaluationEpisode

        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        episode = EvaluationEpisode(
            decision_id="d1",
            symbol="NVDA",
            action="BUY",
            decision_at=now,
            data_as_of=now + timedelta(minutes=5),
            reference_price=100.0,
            benchmark_price=500.0,
        )

        with self.assertRaises(point_in_time.PointInTimeViolation):
            point_in_time.validate_episode_point_in_time(episode)


def _outcome(excess, *, horizon="1d", index=0):
    from datetime import datetime, timedelta, timezone

    return EvaluationOutcome(
        decision_id=f"d{index}",
        horizon=horizon,
        outcome_at=datetime(2026, 6, 1, tzinfo=timezone.utc) + timedelta(days=index),
        asset_price=101.0,
        benchmark_price=100.0,
        asset_return_pct=excess,
        benchmark_return_pct=0.0,
        excess_return_pct=excess,
        directionally_correct=excess > 0,
        estimated_cost_pct=0.1,
    )


class PromotionEdgeTests(unittest.TestCase):
    def test_a_mixed_horizon_input_is_refused(self):
        """Comparing a 1d result against a 20d one is not a comparison."""
        with self.assertRaises(ValueError):
            promotion.assess_promotion(
                [_outcome(1.0, horizon="1d"), _outcome(1.0, horizon="20d")],
                [_outcome(0.1)],
                horizon="1d",
            )

    def test_a_blank_horizon_is_refused(self):
        with self.assertRaises(ValueError):
            promotion.assess_promotion([], [], horizon="  ")

    def test_no_outcomes_reads_as_insufficient_data(self):
        decision = promotion.assess_promotion([], [], horizon="1d")

        self.assertEqual(decision.status, promotion.PromotionStatus.INSUFFICIENT_DATA)

    def test_a_clearly_better_challenger_is_eligible(self):
        challenger = [_outcome(2.0, index=i) for i in range(40)]
        champion = [_outcome(0.1, index=i) for i in range(40)]

        decision = promotion.assess_promotion(challenger, champion, horizon="1d")

        self.assertEqual(decision.status, promotion.PromotionStatus.ELIGIBLE)
        self.assertGreater(decision.uplift_pct, 0)

    def test_a_worse_challenger_is_rejected(self):
        challenger = [_outcome(-1.0, index=i) for i in range(40)]
        champion = [_outcome(1.0, index=i) for i in range(40)]

        decision = promotion.assess_promotion(challenger, champion, horizon="1d")

        self.assertEqual(decision.status, promotion.PromotionStatus.REJECTED)
        self.assertTrue(decision.reasons)


class StyleTests(unittest.TestCase):
    def test_the_stylesheet_is_loadable_and_non_empty(self):
        from webui.utils import styles

        self.assertIsInstance(styles.CSS, str)
        self.assertIn("{", styles.CSS)


class PromptHeaderTests(unittest.TestCase):
    def test_a_report_header_carries_its_prompt_button(self):
        from webui.components.prompt_modal import (
            create_report_header_with_prompt_button,
        )

        rendered = str(
            create_report_header_with_prompt_button("Market Analysis", "market_report")
        )

        self.assertIn("Market Analysis", rendered)
        self.assertIn("show-prompt-btn", rendered)


if __name__ == "__main__":
    unittest.main()
