"""Tests for the replay and promotion callbacks.

Replay is the one action in the workbench that spends real money, so what
matters most here is that it is only ever started by a deliberate click,
refuses when it would corrupt a running analysis, and reports honestly what
it produced.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import dash

from conftest import dash_callback
from tradingagents.evaluation import ExperimentVariant
from tradingagents.workbench.replay import ReplayJob, ReplayOutcome, ReplayRequest
from tradingagents.workbench.tape import build_tape
from webui.callbacks import replay_callbacks

NOW = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)

VARIANTS = [
    ExperimentVariant(experiment_id="champion", execution_eligible=True),
    ExperimentVariant(
        experiment_id="deep",
        execution_eligible=False,
        config_overrides={"research_depth": "Deep"},
    ),
]


def _summary(**overrides):
    fields = {
        "decision_id": "decision-1",
        "symbol": "NVDA",
        "status": "succeeded",
        "created_at": NOW,
        "updated_at": NOW,
        "broker": "alpaca",
        "order_count": 1,
        "filled_quantity": 10.0,
        "error": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _tape(trade_date="2026-09-09"):
    return build_tape(
        summary=_summary(),
        analysis={"trade_date": trade_date} if trade_date else {},
    )


def _request(**overrides):
    fields = {
        "origin_decision_id": "decision-1",
        "symbol": "NVDA",
        "trade_date": "2026-09-09",
        "experiment_id": "deep",
    }
    fields.update(overrides)
    return ReplayRequest(**fields)


def _job(status="done", **outcome_overrides):
    fields = {
        "request": _request(),
        "replay_decision_id": "replay-1",
        "started_at": NOW,
        "action": "BUY",
        "episode_recorded": True,
    }
    fields.update(outcome_overrides)
    return ReplayJob(
        job_id="job-1",
        request=fields["request"],
        status=status,
        queued_at=NOW,
        outcome=ReplayOutcome(**fields),
    )


class VariantOptionTests(unittest.TestCase):
    def test_variants_are_offered_with_whether_they_execute(self):
        with mock.patch.object(replay_callbacks, "variants_from_env", lambda: VARIANTS):
            options = replay_callbacks.variant_options()

        self.assertIn("(executes)", options[0]["label"])
        self.assertIn("(shadow)", options[1]["label"])

    def test_a_variant_is_found_by_its_id(self):
        with mock.patch.object(replay_callbacks, "variants_from_env", lambda: VARIANTS):
            variant = replay_callbacks.variant_by_id("deep")

        self.assertEqual(variant.config_overrides["research_depth"], "Deep")

    def test_an_unknown_variant_is_not_found(self):
        with mock.patch.object(replay_callbacks, "variants_from_env", lambda: VARIANTS):
            self.assertIsNone(replay_callbacks.variant_by_id("nope"))


class RequestBuildingTests(unittest.TestCase):
    def _build(self, *, tape=None, experiment_id="deep", load_error=None):
        def load(_decision_id):
            if load_error:
                raise load_error
            return tape

        with mock.patch.object(replay_callbacks, "variants_from_env", lambda: VARIANTS):
            with mock.patch(
                "webui.callbacks.workbench_callbacks.load_tape", load
            ):
                return replay_callbacks.build_request("decision-1", experiment_id)

    def test_a_request_carries_the_original_symbol_and_date(self):
        request = self._build(tape=_tape())

        self.assertEqual(request.symbol, "NVDA")
        self.assertEqual(request.trade_date, "2026-09-09")
        self.assertEqual(request.origin_decision_id, "decision-1")

    def test_the_variant_overrides_are_carried(self):
        request = self._build(tape=_tape())

        self.assertEqual(request.config_overrides["research_depth"], "Deep")

    def test_the_original_decision_time_is_carried(self):
        """The challenger is scored from the same point as the champion."""
        request = self._build(tape=_tape())

        self.assertEqual(request.decision_at, NOW)

    def test_an_unknown_variant_is_refused_by_name(self):
        self.assertIn("nope", self._build(tape=_tape(), experiment_id="nope"))

    def test_a_decision_with_no_trade_date_is_refused(self):
        message = self._build(tape=_tape(trade_date=None))

        self.assertIn("no recorded trade date", message)

    def test_without_postgres_replay_is_refused(self):
        self.assertIn("PostgreSQL", self._build(tape=None))

    def test_a_failing_lookup_is_reported(self):
        message = self._build(load_error=RuntimeError("connection reset"))

        self.assertIn("Unable to load decision", message)


class JobRenderTests(unittest.TestCase):
    def test_a_finished_replay_reports_what_it_decided(self):
        rendered = str(replay_callbacks.render_jobs([_job()]))

        self.assertIn("DONE", rendered)
        self.assertIn("decided BUY", rendered)
        self.assertIn("shadow episode recorded", rendered)

    def test_a_replay_of_a_past_date_is_flagged(self):
        rendered = str(
            replay_callbacks.render_jobs([_job(point_in_time_verified=False)])
        )

        self.assertIn("may have seen information", rendered)

    def test_a_hold_reports_that_there_is_nothing_to_score(self):
        rendered = str(
            replay_callbacks.render_jobs(
                [
                    _job(
                        action="HOLD",
                        episode_recorded=False,
                        note="nothing to score",
                    )
                ]
            )
        )

        self.assertIn("nothing to score", rendered)

    def test_a_failed_replay_reports_its_error(self):
        job = _job(status="failed", error="provider outage")
        job.error = "provider outage"

        rendered = str(replay_callbacks.render_jobs([job]))

        self.assertIn("FAILED", rendered)
        self.assertIn("provider outage", rendered)

    def test_no_replays_says_so(self):
        self.assertIn("No replay", str(replay_callbacks.render_jobs([])))


class PromotionRenderTests(unittest.TestCase):
    def _view(self, **overrides):
        from tradingagents.evaluation import (
            ExperimentScorecard,
            PromotionDecision,
            PromotionStatus,
        )
        from tradingagents.workbench.promotion_view import PromotionView

        scorecard = ExperimentScorecard(
            count=40,
            hit_rate_pct=62.0,
            mean_asset_return_pct=1.8,
            mean_excess_return_pct=1.2,
            mean_cost_pct=0.1,
            excess_return_stddev_pct=0.8,
            excess_return_lcb_pct=0.4,
        )
        fields = {
            "challenger": "deep",
            "champion": "champion",
            "horizon": "1d",
            "include_replays": False,
            "decision": PromotionDecision(
                status=PromotionStatus.ELIGIBLE,
                horizon="1d",
                challenger=scorecard,
                champion=scorecard.model_copy(update={"mean_excess_return_pct": 0.1}),
                uplift_pct=1.1,
                uplift_lcb_pct=0.3,
                reasons=["Every gate passed."],
            ),
        }
        fields.update(overrides)
        return PromotionView(**fields)

    def test_the_verdict_and_uplift_are_rendered(self):
        rendered = str(replay_callbacks.render_promotion(self._view()))

        self.assertIn("deep vs champion", rendered)
        self.assertIn("ELIGIBLE", rendered)
        self.assertIn("+1.10%", rendered)

    def test_both_scorecards_are_laid_out(self):
        rendered = str(replay_callbacks.render_promotion(self._view()))

        self.assertIn("Hit rate", rendered)
        self.assertIn("Mean excess", rendered)

    def test_the_policy_reasons_are_shown(self):
        rendered = str(replay_callbacks.render_promotion(self._view()))

        self.assertIn("Every gate passed.", rendered)

    def test_the_replay_share_is_stated_when_replays_are_included(self):
        view = self._view(include_replays=True, challenger_replays=30)
        view.notes = ["30 of 40 challenger outcomes came from replays"]

        self.assertIn("came from replays", str(replay_callbacks.render_promotion(view)))

    def test_an_unavailable_comparison_says_why(self):
        view = self._view(decision=None)
        view.unavailable = "Pick a horizon."

        self.assertIn("Pick a horizon.", str(replay_callbacks.render_promotion(view)))

    def test_without_postgres_the_requirement_is_stated(self):
        self.assertIn("PostgreSQL", str(replay_callbacks.render_promotion(None)))


class ReplayCallbackTests(unittest.TestCase):
    def setUp(self):
        app = dash.Dash(__name__, suppress_callback_exceptions=True)
        replay_callbacks.register_replay_callbacks(app)
        self.app = app

        self.state = SimpleNamespace(analysis_running=False)
        patcher = mock.patch.object(replay_callbacks, "app_state", self.state)
        patcher.start()
        self.addCleanup(patcher.stop)

        variants = mock.patch.object(
            replay_callbacks, "variants_from_env", lambda: VARIANTS
        )
        variants.start()
        self.addCleanup(variants.stop)

    def _jobs(self, *, submitted=None, refuse=None, recent=()):
        jobs = mock.MagicMock()
        jobs.recent.return_value = list(recent)
        if refuse:
            jobs.submit.side_effect = refuse
        else:
            jobs.submit.return_value = submitted or _job()
        patcher = mock.patch.object(
            replay_callbacks, "get_replay_jobs", lambda: jobs
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return jobs

    def _click(self, *, trigger="workbench-replay", decision="decision-1",
               variant="deep", clicks=1, request=None):
        def build(decision_id, experiment_id):
            return request if request is not None else _request()

        with mock.patch.object(replay_callbacks, "build_request", build):
            with mock.patch(
                "dash.ctx", SimpleNamespace(triggered_id=trigger)
            ):
                return dash_callback(self.app, "workbench-replay-status.children")(
                    clicks, 0, decision, variant
                )

    def test_clicking_replay_submits_a_job(self):
        jobs = self._jobs()

        self._click()

        jobs.submit.assert_called_once()
        self.assertEqual(jobs.submit.call_args.args[0].experiment_id, "deep")

    def test_a_running_analysis_is_reported_to_the_job_runner(self):
        """It refuses there, because tool config is shared process-wide."""
        jobs = self._jobs()
        self.state.analysis_running = True

        self._click()

        self.assertTrue(jobs.submit.call_args.kwargs["analysis_running"])

    def test_a_refusal_is_shown_rather_than_swallowed(self):
        from tradingagents.workbench.replay import ReplayRefused

        self._jobs(refuse=ReplayRefused("A replay is already running."))

        rendered = str(self._click())

        self.assertIn("already running", rendered)

    def test_the_periodic_tick_reports_progress_without_starting_anything(self):
        """Otherwise the poll would spend money every few seconds."""
        jobs = self._jobs(recent=[_job()])

        rendered = str(self._click(trigger="workbench-replay-interval"))

        jobs.submit.assert_not_called()
        self.assertIn("DONE", rendered)

    def test_no_click_starts_nothing(self):
        jobs = self._jobs()

        self._click(clicks=0)

        jobs.submit.assert_not_called()

    def test_a_missing_decision_or_variant_is_refused(self):
        jobs = self._jobs()

        self.assertIn("Pick a decision", str(self._click(decision=None)))
        self.assertIn("Pick a decision", str(self._click(variant=None)))
        jobs.submit.assert_not_called()

    def test_an_unbuildable_request_is_reported(self):
        jobs = self._jobs()

        rendered = str(self._click(request="That decision has no recorded trade date."))

        jobs.submit.assert_not_called()
        self.assertIn("no recorded trade date", rendered)


class PromotionCallbackTests(ReplayCallbackTests):
    def _promotion(self, view=None, error=None):
        def load(challenger, champion, horizon, include_replays):
            if error:
                raise error
            return view

        with mock.patch.object(replay_callbacks, "load_promotion", load):
            return dash_callback(self.app, "workbench-promotion.children")(
                "deep", "champion", "1d", [], ""
            )

    def test_the_verdict_is_rendered(self):
        from tradingagents.workbench.promotion_view import PromotionView

        view = PromotionView(
            challenger="deep",
            champion="champion",
            horizon="1d",
            include_replays=False,
            unavailable="Not enough data yet.",
        )

        self.assertIn("Not enough data yet.", str(self._promotion(view)))

    def test_a_failing_assessment_reports_the_reason(self):
        rendered = str(self._promotion(error=RuntimeError("connection reset")))

        self.assertIn("Unable to assess promotion", rendered)
        self.assertIn("connection reset", rendered)

    def test_without_postgres_the_requirement_is_stated(self):
        self.assertIn("PostgreSQL", str(self._promotion(None)))


class FilterCallbackTests(ReplayCallbackTests):
    def _filters(self, experiments=(), horizons=(), error=None, **selected):
        def load_experiments():
            if error:
                raise error
            return [{"label": item, "value": item} for item in experiments]

        def load_horizons():
            return [{"label": item, "value": item} for item in horizons]

        with mock.patch.object(
            replay_callbacks, "experiment_options", load_experiments
        ):
            with mock.patch.object(
                replay_callbacks, "horizon_options", load_horizons
            ):
                return dash_callback(self.app, "workbench-challenger.options")(
                    0,
                    "",
                    selected.get("challenger"),
                    selected.get("champion"),
                    selected.get("horizon"),
                )

    def test_the_two_pickers_default_to_different_experiments(self):
        """Comparing an experiment against itself proves nothing."""
        (_c_options, challenger, _p_options, champion, *_rest) = self._filters(
            experiments=["champion", "deep"], horizons=["1d"]
        )

        self.assertNotEqual(challenger, champion)

    def test_a_single_experiment_is_offered_to_both(self):
        (*_options, challenger, _p_options, champion, _h_options, _h) = (
            None, None, *self._filters(experiments=["only"], horizons=["1d"])[1:]
        )

        self.assertEqual(challenger, "only")
        self.assertEqual(champion, "only")

    def test_an_existing_selection_survives_a_refresh(self):
        (_c_options, challenger, *_rest) = self._filters(
            experiments=["champion", "deep"], horizons=["1d"], challenger="champion"
        )

        self.assertEqual(challenger, "champion")

    def test_a_failing_lookup_offers_nothing_rather_than_breaking(self):
        result = self._filters(error=RuntimeError("connection reset"))

        self.assertEqual(result, ([], None, [], None, [], None))

    def test_the_variant_picker_defaults_to_the_first_variant(self):
        options, value = dash_callback(self.app, "workbench-variant.options")(0, None)

        self.assertEqual(value, "champion")
        self.assertEqual(len(options), 2)

    def test_an_existing_variant_choice_survives_a_refresh(self):
        _options, value = dash_callback(self.app, "workbench-variant.options")(0, "deep")

        self.assertEqual(value, "deep")


if __name__ == "__main__":
    unittest.main()


class LedgerLookupTests(unittest.TestCase):
    """The pickers read what the evaluation ledger actually holds."""

    def _runtime(self, *, experiments=(), outcomes=(), postgres=True):
        if not postgres:
            runtime = SimpleNamespace(unit_of_work_factory=None)
        else:
            evaluation = SimpleNamespace(
                experiment_ids=lambda: list(experiments),
                outcomes=lambda **kwargs: list(outcomes),
            )
            uow = mock.MagicMock()
            uow.__enter__ = lambda _self: SimpleNamespace(evaluation=evaluation)
            uow.__exit__ = lambda *a: False
            runtime = SimpleNamespace(unit_of_work_factory=lambda: uow)
        patcher = mock.patch.object(
            replay_callbacks, "get_persistence_runtime", lambda: runtime
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_recorded_experiments_are_offered(self):
        self._runtime(experiments=["champion", "deep"])

        self.assertEqual(
            [option["value"] for option in replay_callbacks.experiment_options()],
            ["champion", "deep"],
        )

    def test_resolved_horizons_are_offered_once_each(self):
        self._runtime(
            outcomes=[
                SimpleNamespace(horizon="1d"),
                SimpleNamespace(horizon="1d"),
                SimpleNamespace(horizon="20d"),
            ]
        )

        self.assertEqual(
            [option["value"] for option in replay_callbacks.horizon_options()],
            ["1d", "20d"],
        )

    def test_without_postgres_nothing_is_offered(self):
        self._runtime(postgres=False)

        self.assertEqual(replay_callbacks.experiment_options(), [])
        self.assertEqual(replay_callbacks.horizon_options(), [])

    def test_the_promotion_query_reaches_the_repository(self):
        captured = {}

        def build(uow, **kwargs):
            captured.update(kwargs)
            return "view"

        self._runtime(experiments=["champion"])
        with mock.patch.object(replay_callbacks, "build_promotion_view", build):
            result = replay_callbacks.load_promotion("deep", "champion", "1d", True)

        self.assertEqual(result, "view")
        self.assertEqual(captured["challenger"], "deep")
        self.assertTrue(captured["include_replays"])

    def test_without_postgres_no_promotion_is_assessed(self):
        self._runtime(postgres=False)

        self.assertIsNone(
            replay_callbacks.load_promotion("deep", "champion", "1d", False)
        )
