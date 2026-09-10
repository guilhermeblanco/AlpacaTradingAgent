"""Tests for why the workers looked dead when they were not.

"1/8 live" was two bugs wearing one number.

The denominator was rows in `service_heartbeats`, keyed
`(service, instance_id)`, where the id was `f"evaluation-{uuid4()}"` — a
fresh identity per process. Nothing ever deleted a row, so four
redeploys left eight, and a counter that reported rows reported
deployment history.

The numerator was a hardcoded 120-second staleness window applied to a
worker that heartbeats once per 300-second cycle. It could never be
anything but stale, while its own container healthcheck, at 900, called
it healthy the whole time.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from tradingagents.operations.services import (
    DEFAULT_STALE_AFTER_SECONDS,
    PROFILES,
    instance_id,
    profile,
    restart_requested,
    stale_after,
)

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


class IdentityTests(unittest.TestCase):
    """Stable, so a restart reuses its row instead of leaving one."""

    def test_the_same_host_gets_the_same_id_twice(self):
        self.assertEqual(
            instance_id("evaluation-worker"), instance_id("evaluation-worker")
        )

    def test_a_replica_index_separates_two_of_the_same_worker(self):
        with mock.patch.dict("os.environ", {"REPLICA_ID": "3"}):
            scaled = instance_id("evaluation-worker")

        self.assertTrue(scaled.endswith("-3"))
        self.assertNotEqual(scaled, instance_id("evaluation-worker"))

    def test_an_explicit_id_wins(self):
        with mock.patch.dict(
            "os.environ", {"EVALUATION_WORKER_INSTANCE_ID": "chosen"}
        ):
            self.assertEqual(instance_id("evaluation-worker"), "chosen")

    def test_the_service_name_is_normalised_into_the_variable(self):
        """`evaluation-worker` reads its id from
        EVALUATION_WORKER_INSTANCE_ID, not EVALUATION-WORKER_..."""
        with mock.patch.dict(
            "os.environ", {"RECONCILIATION_WORKER_INSTANCE_ID": "r1"}
        ):
            self.assertEqual(instance_id("reconciliation-worker"), "r1")


class ToleranceTests(unittest.TestCase):
    """Each service judged against its own cadence, not one number."""

    def test_the_evaluation_worker_may_be_quiet_between_cycles(self):
        """It beats once per 300-second cycle. A 120-second window made
        it permanently dead in the UI and permanently fine to its own
        container healthcheck."""
        self.assertGreater(stale_after("evaluation-worker"), 300)

    def test_the_reconciler_is_held_to_a_much_tighter_window(self):
        """It beats every five seconds; a minute of silence is real."""
        self.assertLess(stale_after("reconciliation-worker"), 120)

    def test_the_tolerance_follows_a_changed_cadence(self):
        slower = stale_after(
            "evaluation-worker", {"evaluation_worker_interval_seconds": "900"}
        )

        self.assertGreater(slower, 900)

    def test_an_unreadable_cadence_falls_back_rather_than_throwing(self):
        self.assertEqual(
            stale_after("evaluation-worker", {"evaluation_worker_interval_seconds": "soon"}),
            stale_after("evaluation-worker"),
        )

    def test_an_unregistered_service_still_gets_a_tolerance(self):
        self.assertEqual(stale_after("mystery-worker"), DEFAULT_STALE_AFTER_SECONDS)

    def test_every_tolerance_allows_at_least_three_missed_beats(self):
        """One missed beat is a slow cycle. Reporting that as a fault
        teaches an operator to ignore the indicator."""
        for item in PROFILES:
            with self.subTest(service=item.name):
                self.assertGreater(item.stale_after_seconds, item.beat_seconds * 3)

    def test_the_optional_worker_is_marked_as_optional(self):
        """Its absence is a configuration, not a fault."""
        self.assertFalse(profile("autonomous-worker").expected)
        self.assertTrue(profile("evaluation-worker").expected)


class RestartRequestTests(unittest.TestCase):
    """A row, not a signal — the web process cannot reach a sibling."""

    def _factory(self, requested_at):
        operations = mock.Mock()
        operations.restart_requested_at.return_value = requested_at
        uow = mock.MagicMock()
        uow.__enter__.return_value = mock.Mock(operations=operations)
        return lambda: uow

    def test_a_request_after_startup_is_honoured(self):
        self.assertTrue(
            restart_requested(
                self._factory(NOW), "evaluation-worker", NOW - timedelta(minutes=5)
            )
        )

    def test_a_request_from_before_startup_is_not(self):
        """Otherwise every worker restarts forever after one click."""
        self.assertFalse(
            restart_requested(
                self._factory(NOW - timedelta(hours=1)),
                "evaluation-worker",
                NOW,
            )
        )

    def test_no_request_means_carry_on(self):
        self.assertFalse(
            restart_requested(self._factory(None), "evaluation-worker", NOW)
        )

    def test_a_naive_timestamp_is_read_as_utc(self):
        """SQLite hands back naive datetimes; comparing one to an aware
        one raises, and a worker must not die of a bookkeeping error."""
        naive = NOW.replace(tzinfo=None)

        self.assertTrue(
            restart_requested(
                self._factory(naive), "evaluation-worker", NOW - timedelta(minutes=5)
            )
        )

    def test_an_unreachable_database_is_not_a_reason_to_stop(self):
        def broken():
            raise RuntimeError("connection refused")

        self.assertFalse(
            restart_requested(broken, "evaluation-worker", NOW)
        )

    def test_no_persistence_at_all_is_not_a_reason_to_stop(self):
        self.assertFalse(restart_requested(None, "evaluation-worker", NOW))


class WorkerAdoptionTests(unittest.TestCase):
    """All three loops honour it, or the button lies for some of them."""

    def test_every_long_lived_worker_checks_for_a_restart(self):
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent
        for path in (
            "tradingagents/evaluation/worker.py",
            "tradingagents/execution/reconciliation_worker.py",
            "tradingagents/orchestration/autonomous_worker.py",
        ):
            with self.subTest(worker=path):
                self.assertIn("restart_requested", (root / path).read_text())

    def test_no_worker_still_mints_a_uuid_identity(self):
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent
        for path in (
            "tradingagents/evaluation/worker.py",
            "tradingagents/execution/reconciliation_worker.py",
        ):
            source = (root / path).read_text()
            with self.subTest(worker=path):
                self.assertNotIn("uuid4()}", source)


class SettingsReachabilityTests(unittest.TestCase):
    """The limits that had no way to be set."""

    def test_the_daily_token_budget_is_settable(self):
        """It has been a validated config field since the safety guard
        was written, with no environment variable and no UI — editing
        the source was the only way to change it."""
        from tradingagents.setup.settings import setting

        self.assertIsNotNone(setting("daily_llm_token_budget"))

    def test_the_concurrency_knobs_are_settable(self):
        from tradingagents.setup.settings import setting

        for key in ("autonomous_max_concurrency", "autonomous_provider_concurrency"):
            with self.subTest(key=key):
                self.assertIsNotNone(setting(key))

    def test_concurrency_changes_the_scheduler_shape(self):
        """The executor is built with it, so a change cannot be picked
        up by simply reading it again next cycle."""
        from tradingagents.orchestration.autonomous_worker import SCHEDULER_SHAPING

        self.assertIn("autonomous_max_concurrency", SCHEDULER_SHAPING)

    def test_a_nonsense_concurrency_falls_back_rather_than_crashing(self):
        from tradingagents.orchestration.autonomous_worker import _positive_int

        for value in ("", None, "many", "0", "-4"):
            with self.subTest(value=value):
                self.assertEqual(_positive_int(value, 2), 2)

    def test_a_real_concurrency_is_used(self):
        from tradingagents.orchestration.autonomous_worker import _positive_int

        self.assertEqual(_positive_int("6", 2), 6)


if __name__ == "__main__":
    unittest.main()
