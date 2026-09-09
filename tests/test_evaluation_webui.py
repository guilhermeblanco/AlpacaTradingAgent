"""The evaluation ledger and promotion gates had no UI surface at all."""

import unittest
from datetime import datetime, timedelta, timezone

from tradingagents.evaluation.models import EvaluationOutcome


def _outcome(*, horizon="1d", excess=1.0, correct=True, cost=0.1, index=0):
    return EvaluationOutcome(
        decision_id=f"decision-{horizon}-{index}",
        horizon=horizon,
        outcome_at=datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(days=index),
        asset_price=101.0,
        benchmark_price=100.0,
        asset_return_pct=excess,
        benchmark_return_pct=0.0,
        excess_return_pct=excess,
        directionally_correct=correct,
        estimated_cost_pct=cost,
    )


class EvaluationPanelTests(unittest.TestCase):
    def test_panel_exposes_filters_and_result_areas(self):
        from webui.components.evaluation_panel import create_evaluation_panel

        rendered = str(create_evaluation_panel())
        for component_id in (
            "evaluation-horizon",
            "evaluation-challenger",
            "evaluation-champion",
            "evaluation-min-outcomes",
            "evaluation-summary",
            "evaluation-promotion",
        ):
            self.assertIn(component_id, rendered)

    def test_callbacks_register(self):
        import dash

        from webui.callbacks.evaluation_callbacks import register_evaluation_callbacks

        app = dash.Dash(__name__, suppress_callback_exceptions=True)
        register_evaluation_callbacks(app)
        outputs = " ".join(app.callback_map)
        self.assertIn("evaluation-summary.children", outputs)
        self.assertIn("evaluation-promotion.children", outputs)

    def test_summary_reports_hit_rate_and_excess_return(self):
        from webui.callbacks.evaluation_callbacks import summarize

        outcomes = [
            _outcome(excess=2.0, correct=True, index=0),
            _outcome(excess=-1.0, correct=False, index=1),
            _outcome(horizon="5d", excess=9.0, correct=True, index=2),
        ]

        rendered = str(summarize(outcomes, "1d"))

        self.assertIn("Hit rate", rendered)
        self.assertIn("50.0%", rendered)
        # Mean excess over the 1d horizon only; the 5d row must not leak in.
        self.assertIn("+0.50%", rendered)
        self.assertNotIn("+9.00%", rendered)

    def test_summary_without_outcomes_says_so(self):
        from webui.callbacks.evaluation_callbacks import summarize

        self.assertIn("No resolved outcomes", str(summarize([], "1d")))

    def test_promotion_reports_eligible_when_every_gate_passes(self):
        from webui.callbacks.evaluation_callbacks import assess

        challenger = [_outcome(excess=2.0, index=i) for i in range(40)]
        champion = [_outcome(excess=0.1, index=i) for i in range(40)]

        rendered = str(
            assess(
                challenger,
                champion,
                horizon="1d",
                challenger="variant-b",
                champion="default",
                min_outcomes=30,
            )
        )

        self.assertIn("ELIGIBLE", rendered)
        self.assertIn("variant-b vs default", rendered)
        self.assertIn("promotion gates passed", rendered)

    def test_promotion_reports_the_gate_that_blocked_it(self):
        from webui.callbacks.evaluation_callbacks import assess

        rendered = str(
            assess(
                [_outcome(excess=2.0, index=i) for i in range(3)],
                [_outcome(excess=0.1, index=i) for i in range(3)],
                horizon="1d",
                challenger="variant-b",
                champion="default",
                min_outcomes=30,
            )
        )

        self.assertIn("INSUFFICIENT DATA", rendered)
        self.assertIn("30 required", rendered)

    def test_promotion_ignores_other_horizons(self):
        """assess_promotion rejects mixed horizons, so they must be filtered."""
        from webui.callbacks.evaluation_callbacks import assess

        challenger = [_outcome(excess=2.0, index=i) for i in range(40)]
        challenger.append(_outcome(horizon="20d", excess=50.0, index=99))

        rendered = str(
            assess(
                challenger,
                [_outcome(excess=0.1, index=i) for i in range(40)],
                horizon="1d",
                challenger="variant-b",
                champion="default",
                min_outcomes=30,
            )
        )

        self.assertNotIn("Unable to assess", rendered)
        self.assertIn("ELIGIBLE", rendered)

    def test_promotion_requires_two_distinct_variants(self):
        from webui.callbacks.evaluation_callbacks import assess

        same = str(
            assess(
                [],
                [],
                horizon="1d",
                challenger="default",
                champion="default",
                min_outcomes=30,
            )
        )
        missing = str(
            assess(
                [], [], horizon="1d", challenger=None, champion="default", min_outcomes=30
            )
        )

        self.assertIn("must differ", same)
        self.assertIn("Select a challenger", missing)


if __name__ == "__main__":
    unittest.main()
