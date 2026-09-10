"""Tests for variant replay and the promotion gate it feeds.

The gate-only preview asks "would this still trade?". A variant replay asks
the larger question: would a different configuration have decided
differently, and would it have been better? That means re-running the
analysts, so three properties matter more than any other — a replay never
executes, it is scored from the original decision's timestamp, and a replay
of a past date is marked because it can see what the original run could
not.
"""

from __future__ import annotations

import threading
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from tradingagents.evaluation import ExperimentVariant
from tradingagents.workbench.replay import (
    REPLAY_SOURCE,
    ReplayJobs,
    ReplayRefused,
    ReplayRequest,
    run_variant_replay,
    variants_from_env,
)

NOW = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)


def _request(**overrides):
    fields = {
        "origin_decision_id": "decision-1",
        "symbol": "NVDA",
        "trade_date": "2026-09-09",
        "experiment_id": "challenger",
        "config_overrides": {"research_depth": "Deep"},
        "decision_at": NOW - timedelta(days=1),
    }
    fields.update(overrides)
    return ReplayRequest(**fields)


def _intent(action="BUY"):
    from tradingagents.agents.schemas import (
        ExecutableAction,
        RiskDecision,
        build_trade_intent_from_risk_decision,
    )

    return build_trade_intent_from_risk_decision(
        symbol="NVDA",
        trading_mode="investment",
        current_position="NEUTRAL",
        decision=RiskDecision(
            action=ExecutableAction(action),
            confidence="high",
            risk_rationale="test",
            required_controls="stop at 95",
            target_portfolio_pct=1.0,
        ),
        trade_date="2026-09-09",
    ).model_dump(mode="json")


class Graph:
    """Records what it was configured with instead of running agents."""

    built = []

    def __init__(self, analysts, config=None, debug=False):
        Graph.built.append({"analysts": analysts, "config": config})
        self.analysts = analysts
        self.config = config

    def propagate(self, symbol, trade_date):
        Graph.built[-1]["symbol"] = symbol
        Graph.built[-1]["trade_date"] = trade_date
        return {
            "company_of_interest": symbol,
            "trade_date": trade_date,
            "final_trade_intent": self.intent,
        }, "BUY"


class Prices:
    def price_at_or_before(self, symbol, at):
        from tradingagents.evaluation import PriceObservation

        return PriceObservation(
            symbol=symbol, price=100.0, observed_at=at - timedelta(minutes=1)
        )


class ReplayFixture(unittest.TestCase):
    def setUp(self):
        Graph.built = []
        self.recorded = []

        uow = mock.MagicMock()
        uow.__enter__ = lambda _self: SimpleNamespace(
            evaluation=SimpleNamespace(
                record_episode=lambda episode: self.recorded.append(episode)
            ),
            commit=lambda: None,
        )
        uow.__exit__ = lambda *a: False
        self.factory = lambda: uow

        patcher = mock.patch(
            "tradingagents.dataflows.config.get_config",
            lambda: {"research_depth": "Shallow", "max_debate_rounds": 1},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        stages = mock.patch(
            "tradingagents.workbench.analysis_record.record_analysis_stages",
            lambda record: True,
        )
        stages.start()
        self.addCleanup(stages.stop)

    def _run(self, request=None, *, action="BUY", now=NOW, graph=None):
        graph_class = graph or Graph
        graph_class.intent = _intent(action) if action else {}
        return run_variant_replay(
            request or _request(),
            graph_factory=graph_class,
            prices=Prices(),
            unit_of_work_factory=self.factory,
            now=now,
        )


class ConfigurationTests(ReplayFixture):
    def test_the_variant_overrides_the_base_configuration(self):
        self._run(_request(config_overrides={"research_depth": "Deep"}))

        built = Graph.built[0]["config"]
        self.assertEqual(built["research_depth"], "Deep")
        self.assertEqual(built["max_debate_rounds"], 1)

    def test_the_base_configuration_is_not_mutated(self):
        """Two variants in a row must not accumulate each other's overrides."""
        from tradingagents.dataflows.config import get_config

        self._run(_request(config_overrides={"research_depth": "Deep"}))

        self.assertEqual(get_config()["research_depth"], "Shallow")

    def test_the_symbol_and_trade_date_come_from_the_original(self):
        self._run(_request(symbol="AAPL", trade_date="2026-01-15"))

        self.assertEqual(Graph.built[0]["symbol"], "AAPL")
        self.assertEqual(Graph.built[0]["trade_date"], "2026-01-15")

    def test_the_analyst_roster_is_carried(self):
        self._run(_request(analysts=["market", "news"]))

        self.assertEqual(Graph.built[0]["analysts"], ["market", "news"])


class EpisodeTests(ReplayFixture):
    def test_an_exposure_adding_decision_records_a_shadow_episode(self):
        outcome = self._run(action="BUY")

        self.assertTrue(outcome.episode_recorded)
        self.assertTrue(outcome.succeeded)
        self.assertEqual(len(self.recorded), 1)

    def test_the_episode_is_tagged_with_the_experiment(self):
        self._run()

        self.assertEqual(self.recorded[0].experiment_id, "challenger")

    def test_the_episode_is_marked_as_a_replay_of_its_original(self):
        self._run()

        metadata = self.recorded[0].metadata
        self.assertEqual(metadata["origin"], REPLAY_SOURCE)
        self.assertEqual(metadata["origin_decision_id"], "decision-1")
        self.assertEqual(metadata["experiment_role"], "shadow")

    def test_the_episode_is_scored_from_the_original_decision_time(self):
        """Scoring a challenger from today against a champion from three days
        ago would not be a comparison."""
        original = NOW - timedelta(days=3)

        self._run(_request(decision_at=original))

        self.assertEqual(self.recorded[0].decision_at, original)

    def test_without_a_recorded_time_the_replay_scores_from_now(self):
        self._run(_request(decision_at=None))

        self.assertEqual(self.recorded[0].decision_at, NOW)

    def test_a_hold_records_nothing_and_says_why(self):
        outcome = self._run(action="HOLD")

        self.assertFalse(outcome.episode_recorded)
        self.assertEqual(self.recorded, [])
        self.assertIn("nothing to score", outcome.note)
        self.assertTrue(outcome.succeeded)

    def test_a_run_producing_no_intent_records_nothing(self):
        outcome = self._run(action=None)

        self.assertFalse(outcome.episode_recorded)
        self.assertIsNone(outcome.action)

    def test_the_replay_gets_its_own_decision_id(self):
        outcome = self._run()

        self.assertTrue(outcome.replay_decision_id.startswith("replay-"))
        self.assertNotEqual(outcome.replay_decision_id, "decision-1")
        self.assertEqual(self.recorded[0].decision_id, outcome.replay_decision_id)


class PointInTimeTests(ReplayFixture):
    """Which replays could only see what the original run could.

    Historical replays are clean: every source honours the window and the
    hosted search stands down for a date it cannot constrain. A same-day
    replay is not, because the search runs live and the original decision
    was made earlier in the day.
    """

    def test_replaying_a_past_date_is_marked_verified(self):
        outcome = self._run(_request(trade_date="2026-01-15"), now=NOW)

        self.assertTrue(outcome.point_in_time_verified)
        self.assertTrue(self.recorded[0].metadata["point_in_time_verified"])

    def test_replaying_today_is_marked_unverified(self):
        """Live search can surface news published since the original ran."""
        outcome = self._run(_request(trade_date="2026-09-09"), now=NOW)

        self.assertFalse(outcome.point_in_time_verified)
        self.assertFalse(self.recorded[0].metadata["point_in_time_verified"])

    def test_an_unparseable_date_is_treated_as_unverified(self):
        outcome = self._run(_request(trade_date="whenever"), now=NOW)

        self.assertFalse(outcome.point_in_time_verified)

    def test_forcing_point_in_time_sourcing_verifies_a_same_day_replay(self):
        with mock.patch(
            "tradingagents.dataflows.config.get_config",
            lambda: {"require_point_in_time_web_search": True},
        ):
            outcome = self._run(_request(trade_date="2026-09-09"), now=NOW)

        self.assertTrue(outcome.point_in_time_verified)


class FailureTests(ReplayFixture):
    def test_a_failing_run_is_reported_rather_than_raised(self):
        class Broken(Graph):
            def propagate(self, symbol, trade_date):
                raise RuntimeError("provider outage")

        outcome = self._run(graph=Broken)

        self.assertFalse(outcome.succeeded)
        self.assertIn("provider outage", outcome.error)
        self.assertEqual(self.recorded, [])

    def test_an_unrecordable_episode_does_not_lose_the_run(self):
        def explode(episode):
            raise RuntimeError("point-in-time violation")

        uow = mock.MagicMock()
        uow.__enter__ = lambda _self: SimpleNamespace(
            evaluation=SimpleNamespace(record_episode=explode), commit=lambda: None
        )
        uow.__exit__ = lambda *a: False

        Graph.intent = _intent("BUY")
        outcome = run_variant_replay(
            _request(),
            graph_factory=Graph,
            prices=Prices(),
            unit_of_work_factory=lambda: uow,
            now=NOW,
        )

        self.assertFalse(outcome.episode_recorded)
        self.assertIn("no episode could be recorded", outcome.note)
        self.assertIsNone(outcome.error)

    def test_no_database_is_reported_as_a_missing_episode(self):
        Graph.intent = _intent("BUY")

        with mock.patch(
            "tradingagents.persistence.unit_of_work_factory", lambda: None
        ):
            outcome = run_variant_replay(
                _request(), graph_factory=Graph, prices=Prices(), now=NOW
            )

        self.assertFalse(outcome.episode_recorded)
        self.assertIn("no database", outcome.note)


class NeverExecutesTests(ReplayFixture):
    def test_a_replay_constructs_no_execution_pipeline(self):
        """The single most important property: a replay is not a trade."""
        with mock.patch(
            "tradingagents.execution.pipeline.ExecutionPipeline"
        ) as pipeline:
            with mock.patch(
                "tradingagents.execution.pipeline.execute_autonomous_trade"
            ) as execute:
                self._run(action="BUY")

        pipeline.assert_not_called()
        execute.assert_not_called()


class VariantSourceTests(unittest.TestCase):
    """What you can replay under is what the machine would actually run."""

    def test_the_default_is_a_single_executing_champion(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("AUTONOMOUS_EXPERIMENTS_JSON", None)
            variants = variants_from_env()

        self.assertEqual([item.experiment_id for item in variants], ["champion"])
        self.assertTrue(variants[0].execution_eligible)

    def test_configured_variants_are_read(self):
        payload = (
            '[{"experiment_id":"champion","weight":1,"execution_eligible":true,'
            '"config_overrides":{}},'
            '{"experiment_id":"deep","weight":1,"execution_eligible":false,'
            '"config_overrides":{"research_depth":"Deep"}}]'
        )

        with mock.patch.dict(
            "os.environ", {"AUTONOMOUS_EXPERIMENTS_JSON": payload}
        ):
            variants = variants_from_env()

        self.assertEqual(
            [item.experiment_id for item in variants], ["champion", "deep"]
        )
        self.assertEqual(variants[1].config_overrides["research_depth"], "Deep")

    def test_malformed_configuration_falls_back_rather_than_raising(self):
        with mock.patch.dict(
            "os.environ", {"AUTONOMOUS_EXPERIMENTS_JSON": "not json"}
        ):
            self.assertEqual(
                [item.experiment_id for item in variants_from_env()], ["champion"]
            )

    def test_an_unusable_entry_is_skipped(self):
        payload = '[{"experiment_id":"good"},{"weight":1}]'

        with mock.patch.dict(
            "os.environ", {"AUTONOMOUS_EXPERIMENTS_JSON": payload}
        ):
            self.assertEqual(
                [item.experiment_id for item in variants_from_env()], ["good"]
            )


class JobTests(unittest.TestCase):
    """Replays run one at a time, off the request thread."""

    def _jobs(self, runner=None):
        return ReplayJobs(runner=runner or (lambda request: self._stub_outcome(request)))

    @staticmethod
    def _stub_outcome(request, **overrides):
        from tradingagents.workbench.replay import ReplayOutcome

        fields = {
            "request": request,
            "replay_decision_id": "replay-1",
            "started_at": NOW,
            "episode_recorded": True,
        }
        fields.update(overrides)
        return ReplayOutcome(**fields)

    def _wait(self, jobs, job_id, timeout=2.0):
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = jobs.get(job_id)
            if job and job.finished:
                return job
            time.sleep(0.01)
        raise AssertionError("the replay job never finished")

    def test_a_submitted_replay_runs_and_reports_its_outcome(self):
        jobs = self._jobs()

        job = jobs.submit(_request())

        finished = self._wait(jobs, job.job_id)
        self.assertEqual(finished.status, "done")
        self.assertTrue(finished.outcome.episode_recorded)

    def test_a_failing_replay_is_reported_as_failed(self):
        jobs = self._jobs(
            runner=lambda request: self._stub_outcome(request, error="provider outage")
        )

        job = jobs.submit(_request())

        finished = self._wait(jobs, job.job_id)
        self.assertEqual(finished.status, "failed")
        self.assertIn("provider outage", finished.error)

    def test_a_replay_is_refused_while_an_analysis_is_running(self):
        """Tool configuration is published process-wide; two graphs running
        different variants would overwrite each other."""
        jobs = self._jobs()

        with self.assertRaises(ReplayRefused) as raised:
            jobs.submit(_request(), analysis_running=True)

        self.assertIn("process-wide", str(raised.exception))

    def test_only_one_replay_runs_at_a_time(self):
        gate = threading.Event()
        jobs = self._jobs(
            runner=lambda request: (gate.wait(2.0), self._stub_outcome(request))[1]
        )

        first = jobs.submit(_request())
        try:
            with self.assertRaises(ReplayRefused):
                jobs.submit(_request())
        finally:
            gate.set()
        self._wait(jobs, first.job_id)

    def test_the_slot_is_released_when_a_replay_finishes(self):
        jobs = self._jobs()

        first = jobs.submit(_request())
        self._wait(jobs, first.job_id)

        self.assertIsNone(jobs.running)
        self.assertIsNotNone(jobs.submit(_request()))

    def test_recent_replays_come_back_newest_first(self):
        jobs = self._jobs()

        first = jobs.submit(_request(experiment_id="one"))
        self._wait(jobs, first.job_id)
        second = jobs.submit(_request(experiment_id="two"))
        self._wait(jobs, second.job_id)

        self.assertEqual(
            [job.request.experiment_id for job in jobs.recent()], ["two", "one"]
        )

    def test_the_history_is_bounded(self):
        jobs = ReplayJobs(runner=lambda request: self._stub_outcome(request), max_history=2)

        for index in range(4):
            job = jobs.submit(_request(experiment_id=f"v{index}"))
            self._wait(jobs, job.job_id)

        self.assertLessEqual(len(jobs.recent(limit=10)), 2)

    def test_an_unknown_job_is_not_found(self):
        self.assertIsNone(self._jobs().get("nope"))


if __name__ == "__main__":
    unittest.main()


class PromotionViewTests(unittest.TestCase):
    """The gate, read for one pair, with the replay share stated."""

    def _outcomes(self, count, *, excess=1.5, horizon="1d", correct=True):
        from tradingagents.evaluation.models import EvaluationOutcome

        return [
            EvaluationOutcome(
                decision_id=f"d{index}",
                horizon=horizon,
                outcome_at=NOW + timedelta(days=index),
                asset_price=101.0,
                benchmark_price=100.0,
                asset_return_pct=excess,
                benchmark_return_pct=0.0,
                excess_return_pct=excess,
                directionally_correct=correct,
                estimated_cost_pct=0.1,
            )
            for index in range(count)
        ]

    def _uow(self, *, table=None, error=None, unverified=(), experiments=None):
        def outcomes(*, experiment_id=None, include_replays=True):
            if error:
                raise error
            return list((table or {}).get((experiment_id, include_replays), []))

        if experiments is None:
            experiments = sorted({key[0] for key in (table or {})})

        return SimpleNamespace(
            evaluation=SimpleNamespace(
                outcomes=outcomes,
                unverified_decision_ids=lambda: set(unverified),
                experiment_ids=lambda: list(experiments),
            )
        )

    def _view(self, table, unverified=(), experiments=None, **kwargs):
        from tradingagents.workbench.promotion_view import build_promotion_view

        options = {
            "challenger": "deep",
            "champion": "champion",
            "horizon": "1d",
            "include_replays": False,
        }
        options.update(kwargs)
        return build_promotion_view(
            self._uow(table=table, unverified=unverified, experiments=experiments),
            **options,
        )

    def test_a_clearly_better_challenger_is_eligible(self):
        from tradingagents.evaluation import PromotionStatus

        table = {
            ("deep", False): self._outcomes(40, excess=2.0),
            ("champion", False): self._outcomes(40, excess=0.1),
        }

        view = self._view(table)

        self.assertTrue(view.available)
        self.assertEqual(view.decision.status, PromotionStatus.ELIGIBLE)
        self.assertGreater(view.decision.uplift_pct, 0)

    def test_a_worse_challenger_is_rejected(self):
        from tradingagents.evaluation import PromotionStatus

        table = {
            ("deep", False): self._outcomes(40, excess=-1.0, correct=False),
            ("champion", False): self._outcomes(40, excess=1.0),
        }

        self.assertEqual(
            self._view(table).decision.status, PromotionStatus.REJECTED
        )

    def test_only_the_requested_horizon_is_compared(self):
        table = {
            ("deep", False): [
                *self._outcomes(3, horizon="1d"),
                *self._outcomes(9, horizon="20d"),
            ],
            ("champion", False): self._outcomes(3, horizon="1d"),
        }

        view = self._view(table)

        self.assertEqual(view.decision.challenger.count, 3)

    def test_replays_are_excluded_by_default(self):
        """A challenger built out of replays is not evidence to promote on."""
        table = {
            ("deep", False): self._outcomes(3),
            ("deep", True): self._outcomes(40),
            ("champion", False): self._outcomes(3),
            ("champion", True): self._outcomes(40),
        }

        view = self._view(table)

        self.assertEqual(view.decision.challenger.count, 3)
        self.assertIn("excluded", " ".join(view.notes))

    def test_including_replays_says_how_much_was_replayed(self):
        table = {
            ("deep", False): self._outcomes(10),
            ("deep", True): self._outcomes(40),
            ("champion", False): self._outcomes(40),
            ("champion", True): self._outcomes(40),
        }

        view = self._view(table, include_replays=True)

        self.assertEqual(view.decision.challenger.count, 40)
        self.assertEqual(view.challenger_replays, 30)
        self.assertEqual(view.replay_share_pct, 75.0)
        self.assertIn("came from replays", " ".join(view.notes))
        self.assertIn("selected rather than drawn", " ".join(view.notes))

    def test_a_comparison_with_itself_is_refused(self):
        view = self._view({}, challenger="champion", champion="champion")

        self.assertFalse(view.available)
        self.assertIn("itself", view.unavailable)

    def test_a_missing_side_is_refused(self):
        # `experiments` is supplied because the point here is the missing
        # selection; with nothing recorded the view says something more
        # specific instead, which EmptySelectionTests covers.
        recorded = ["champion", "deep"]

        self.assertIn(
            "Pick a challenger",
            self._view({}, challenger="", experiments=recorded).unavailable,
        )
        self.assertIn(
            "Pick a challenger",
            self._view({}, champion="", experiments=recorded).unavailable,
        )

    def test_a_missing_horizon_is_refused(self):
        self.assertIn("horizon", self._view({}, horizon="").unavailable)

    def test_an_unreadable_ledger_is_reported(self):
        from tradingagents.workbench.promotion_view import build_promotion_view

        view = build_promotion_view(
            self._uow(error=RuntimeError("connection reset")),
            challenger="deep",
            champion="champion",
            horizon="1d",
        )

        self.assertIn("Unable to read outcomes", view.unavailable)

    def test_no_outcomes_reads_as_insufficient_data(self):
        from tradingagents.evaluation import PromotionStatus

        view = self._view({})

        self.assertEqual(
            view.decision.status, PromotionStatus.INSUFFICIENT_DATA
        )

    def test_the_replay_share_of_an_empty_comparison_is_nothing(self):
        self.assertEqual(self._view({}).replay_share_pct, 0.0)


class EmptySelectionTests(PromotionViewTests):
    """A deployment that has recorded nothing yet.

    Every dropdown feeding this view is empty on day one, and an empty Dash
    dropdown's value is None. The view used to require all three as strings,
    so the panel that exists to explain "nothing to compare yet" was itself
    the thing that could not be built — the UI showed three pydantic
    validation errors instead of a sentence.
    """

    def test_no_selection_at_all_is_not_an_error(self):
        view = self._view({}, challenger=None, champion=None, horizon=None)

        self.assertFalse(view.available)
        self.assertEqual(view.challenger, "")
        self.assertEqual(view.champion, "")
        self.assertEqual(view.horizon, "")

    def test_an_empty_ledger_says_so_rather_than_saying_pick_two(self):
        """"Pick a challenger" is unhelpful advice when there is nothing to
        pick from."""
        view = self._view({}, challenger=None, champion=None, experiments=[])

        self.assertIn("No experiment has recorded an outcome yet", view.unavailable)

    def test_a_populated_ledger_asks_for_a_selection(self):
        view = self._view(
            {}, challenger=None, champion=None, experiments=["champion", "deep"]
        )

        self.assertIn("Pick a challenger and a champion", view.unavailable)

    def test_an_unreadable_ledger_falls_back_to_the_general_message(self):
        from tradingagents.workbench.promotion_view import build_promotion_view

        uow = SimpleNamespace(
            evaluation=SimpleNamespace(
                outcomes=lambda **_: [],
                unverified_decision_ids=lambda: set(),
                experiment_ids=lambda: (_ for _ in ()).throw(RuntimeError("down")),
            )
        )

        view = build_promotion_view(
            uow, challenger=None, champion=None, horizon=None
        )

        self.assertIn("Pick a challenger and a champion", view.unavailable)


class ScorecardTests(PromotionViewTests):
    def test_both_sides_are_laid_out_row_by_row(self):
        from tradingagents.workbench.promotion_view import scorecard_rows

        table = {
            ("deep", False): self._outcomes(40, excess=2.0),
            ("champion", False): self._outcomes(40, excess=0.1),
        }

        rows = scorecard_rows(self._view(table))

        self.assertEqual(
            [row["label"] for row in rows],
            ["Outcomes", "Hit rate", "Mean excess", "Excess LCB", "Mean cost"],
        )

    def test_the_better_side_of_each_row_is_marked(self):
        from tradingagents.workbench.promotion_view import scorecard_rows

        table = {
            ("deep", False): self._outcomes(40, excess=2.0),
            ("champion", False): self._outcomes(40, excess=0.1),
        }

        rows = {row["label"]: row for row in scorecard_rows(self._view(table))}

        self.assertTrue(rows["Mean excess"]["better"])

    def test_a_lower_cost_counts_as_better(self):
        """Cost is the one column where less is more."""
        from tradingagents.workbench.promotion_view import scorecard_rows

        table = {
            ("deep", False): self._outcomes(40),
            ("champion", False): self._outcomes(40),
        }

        rows = {row["label"]: row for row in scorecard_rows(self._view(table))}

        self.assertFalse(rows["Mean cost"]["better"])

    def test_an_unavailable_view_has_no_rows(self):
        from tradingagents.workbench.promotion_view import scorecard_rows

        self.assertEqual(scorecard_rows(self._view({}, horizon="")), [])


class LeakageNoteTests(PromotionViewTests):
    """Selection and leakage are separate hazards and are reported apart."""

    def test_unverified_outcomes_are_counted_and_named(self):
        outcomes = self._outcomes(4)
        table = {
            ("deep", False): outcomes,
            ("champion", False): self._outcomes(4),
        }

        view = self._view(table, unverified={"d0", "d1"})

        self.assertEqual(view.challenger_unverified, 2)
        self.assertIn("not fully date-bounded", " ".join(view.notes))

    def test_a_fully_bounded_challenger_carries_no_leakage_note(self):
        table = {
            ("deep", False): self._outcomes(4),
            ("champion", False): self._outcomes(4),
        }

        view = self._view(table)

        self.assertEqual(view.challenger_unverified, 0)
        self.assertNotIn("date-bounded", " ".join(view.notes))

    def test_a_repository_without_the_query_does_not_break_the_view(self):
        """An older ledger may not answer it; the verdict still stands."""
        from tradingagents.workbench.promotion_view import build_promotion_view

        uow = SimpleNamespace(
            evaluation=SimpleNamespace(
                outcomes=lambda **kwargs: self._outcomes(4),
            )
        )

        view = build_promotion_view(
            uow, challenger="deep", champion="champion", horizon="1d"
        )

        self.assertTrue(view.available)
        self.assertEqual(view.challenger_unverified, 0)
