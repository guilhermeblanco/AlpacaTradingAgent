import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tradingagents.evaluation import EvaluationEpisode, EvaluationRepository, calculate_outcome
from tradingagents.evaluation.point_in_time import PointInTimeViolation
from tradingagents.evaluation.reporting import summarize_outcomes


class EvaluationLedgerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
        self.episode = EvaluationEpisode(
            decision_id="decision-1", symbol="AAPL", action="BUY",
            decision_at=self.now, data_as_of=self.now - timedelta(minutes=1),
            reference_price=100, benchmark_price=500, confidence=0.8,
            experiment_id="gpt-test",
        )

    def test_rejects_future_data(self):
        invalid = self.episode.model_copy(update={"data_as_of": self.now + timedelta(seconds=1)})
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PointInTimeViolation):
                EvaluationRepository(Path(tmp) / "eval.sqlite3").record_episode(invalid)

    def test_calculates_cost_adjusted_benchmarked_outcome(self):
        outcome = calculate_outcome(
            self.episode, horizon="5d", outcome_at=self.now + timedelta(days=5),
            asset_price=110, benchmark_price=525, estimated_cost_pct=0.2,
        )
        self.assertAlmostEqual(outcome.asset_return_pct, 9.8)
        self.assertAlmostEqual(outcome.benchmark_return_pct, 5.0)
        self.assertAlmostEqual(outcome.excess_return_pct, 4.8)
        self.assertTrue(outcome.directionally_correct)

    def test_repository_is_idempotent_and_reports_by_experiment(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = EvaluationRepository(Path(tmp) / "eval.sqlite3")
            repository.record_episode(self.episode)
            repository.record_episode(self.episode)
            outcome = calculate_outcome(
                self.episode, horizon="1d", outcome_at=self.now + timedelta(days=1),
                asset_price=102, benchmark_price=505,
            )
            repository.record_outcome(outcome)
            repository.record_outcome(
                outcome.model_copy(update={"asset_return_pct": -99.0})
            )
            rows = repository.outcomes(experiment_id="gpt-test")
            summary = summarize_outcomes(rows)
            self.assertEqual(len(rows), 1)
            self.assertEqual(summary["count"], 1)
            self.assertEqual(summary["hit_rate_pct"], 100.0)
            self.assertAlmostEqual(rows[0].asset_return_pct, 2.0)


if __name__ == "__main__":
    unittest.main()
