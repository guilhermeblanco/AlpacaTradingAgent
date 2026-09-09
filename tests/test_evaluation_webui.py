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


class EvaluationLoadTests(unittest.TestCase):
    """Outcomes come from PostgreSQL; without it the panel says so instead of
    rendering an empty scoreboard that reads like "no edge"."""

    def _runtime(self, outcomes=None, experiments=(), error=None):
        from types import SimpleNamespace
        from unittest import mock

        from webui.callbacks import evaluation_callbacks

        if outcomes is None and error is None:
            runtime = SimpleNamespace(unit_of_work_factory=None)
        else:
            repository = SimpleNamespace(
                outcomes=lambda experiment_id=None: (
                    (_ for _ in ()).throw(error)
                    if error
                    else list(outcomes.get(experiment_id, []))
                    if isinstance(outcomes, dict)
                    else list(outcomes)
                ),
                experiment_ids=lambda: list(experiments),
            )
            uow = mock.MagicMock()
            uow.__enter__ = lambda _self: SimpleNamespace(evaluation=repository)
            uow.__exit__ = lambda *a: False
            runtime = SimpleNamespace(unit_of_work_factory=lambda: uow)

        patcher = mock.patch.object(
            evaluation_callbacks, "get_persistence_runtime", lambda: runtime
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_outcomes_and_experiment_ids_come_back_together(self):
        from webui.callbacks.evaluation_callbacks import load_outcomes

        self._runtime([_outcome()], experiments=["champion", "challenger"])

        outcomes, experiments = load_outcomes()

        self.assertEqual(len(outcomes), 1)
        self.assertEqual(experiments, ["champion", "challenger"])

    def test_without_postgres_there_are_no_outcomes_to_report(self):
        from webui.callbacks.evaluation_callbacks import load_outcomes

        self._runtime()

        self.assertEqual(load_outcomes(), (None, []))


class EvaluationCallbackTests(EvaluationLoadTests):
    def setUp(self):
        import dash

        from webui.callbacks.evaluation_callbacks import register_evaluation_callbacks

        app = dash.Dash(__name__, suppress_callback_exceptions=True)
        register_evaluation_callbacks(app)
        self.app = app

    def _filters(self, horizon=None, challenger=None, champion=None):
        from conftest import dash_callback

        return dash_callback(self.app, "evaluation-horizon.options")(
            0, 0, horizon, challenger, champion
        )

    def _render(self, horizon="1d", challenger=None, champion=None, min_outcomes=30):
        from conftest import dash_callback

        return dash_callback(self.app, "evaluation-summary.children")(
            horizon, challenger, champion, min_outcomes, 0
        )

    def test_the_horizons_present_in_the_data_are_offered(self):
        self._runtime([_outcome(horizon="1d"), _outcome(horizon="20d")])

        horizon_options, horizon, *_rest = self._filters()

        self.assertEqual(
            [option["value"] for option in horizon_options], ["1d", "20d"]
        )
        self.assertEqual(horizon, "1d")

    def test_an_existing_horizon_choice_survives_a_refresh(self):
        self._runtime([_outcome(horizon="1d"), _outcome(horizon="20d")])

        _options, horizon, *_rest = self._filters(horizon="20d")

        self.assertEqual(horizon, "20d")

    def test_a_horizon_that_no_longer_has_data_falls_back(self):
        self._runtime([_outcome(horizon="1d")])

        _options, horizon, *_rest = self._filters(horizon="90d")

        self.assertEqual(horizon, "1d")

    def test_the_two_variant_pickers_default_to_different_experiments(self):
        """Comparing an experiment against itself proves nothing."""
        self._runtime([_outcome()], experiments=["champion", "challenger"])

        (_h_options, _h, _c_options, challenger, _p_options, champion) = self._filters()

        self.assertNotEqual(challenger, champion)

    def test_a_single_experiment_is_offered_to_both_pickers(self):
        self._runtime([_outcome()], experiments=["only"])

        (*_rest, challenger, _p_options, champion) = self._filters()

        self.assertEqual(challenger, "only")
        self.assertEqual(champion, "only")

    def test_without_postgres_no_filters_are_offered(self):
        self._runtime()

        self.assertEqual(self._filters(), ([], None, [], None, [], None))

    def test_the_summary_renders_for_the_selected_horizon(self):
        self._runtime([_outcome(excess=2.0) for _ in range(5)])

        summary, _promotion = self._render()

        self.assertTrue(summary)

    def test_without_postgres_the_requirement_is_stated(self):
        self._runtime()

        summary, promotion = self._render()

        self.assertIn("SETUP REQUIRED", str(summary))
        self.assertIn("requires PostgreSQL", str(promotion))

    def test_no_horizon_selected_renders_the_no_data_placeholder(self):
        self._runtime([_outcome()])

        summary, promotion = self._render(horizon=None)

        self.assertTrue(summary)
        self.assertEqual(promotion, "")

    def test_a_failing_outcome_query_is_reported_rather_than_raised(self):
        self._runtime(error=RuntimeError("connection reset"))

        summary, promotion = self._render()

        self.assertEqual(summary, "")
        self.assertIn("Unable to load outcomes", str(promotion))

    def test_a_promotion_comparison_renders_both_variants(self):
        outcomes = {
            None: [_outcome(index=i) for i in range(40)],
            "challenger": [_outcome(excess=2.0, index=i) for i in range(40)],
            "champion": [_outcome(excess=0.1, index=i) for i in range(40)],
        }
        self._runtime(outcomes, experiments=["champion", "challenger"])

        _summary, promotion = self._render(
            challenger="challenger", champion="champion"
        )

        rendered = str(promotion)
        self.assertIn("challenger vs champion", rendered)
