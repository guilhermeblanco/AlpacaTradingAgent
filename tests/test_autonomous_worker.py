"""The autonomous worker is the unattended production entry point.

It had no tests: a bad env default, a leaked database engine on a startup
failure, or a quarantine scope that disagrees with the trading processes
would only show up in production.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock

from tradingagents.execution.quarantine import (
    execution_quarantine_scope,
    quarantine_scope_from_env,
    resolve_execution_scope,
)


class QuarantineScopeTests(unittest.TestCase):
    """Every process sharing an account must derive the same string.

    A quarantine written under one name while another process reads a
    different one pauses nothing.
    """

    def test_bare_broker_when_no_account_key(self):
        self.assertEqual(execution_quarantine_scope("alpaca"), "execution:alpaca")

    def test_account_key_is_appended(self):
        self.assertEqual(
            execution_quarantine_scope("alpaca", account_key="paper-primary"),
            "execution:alpaca:paper-primary",
        )

    def test_blank_account_key_leaves_no_trailing_separator(self):
        self.assertEqual(
            execution_quarantine_scope("alpaca", account_key="  "), "execution:alpaca"
        )

    def test_configured_scope_wins(self):
        self.assertEqual(
            execution_quarantine_scope(
                "alpaca", account_key="ignored", configured="execution:shared"
            ),
            "execution:shared",
        )

    def test_blank_configured_scope_falls_through(self):
        """env.sample ships EXECUTION_QUARANTINE_SCOPE empty."""
        self.assertEqual(
            execution_quarantine_scope("alpaca", configured="   "), "execution:alpaca"
        )

    def test_missing_broker_is_rejected(self):
        with self.assertRaises(ValueError):
            execution_quarantine_scope("")

    def test_the_monitor_and_the_worker_agree_from_one_environment(self):
        env = {
            "AUTONOMOUS_ACCOUNT_KEY": "paper-primary",
            "EXECUTION_QUARANTINE_SCOPE": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(
                quarantine_scope_from_env("alpaca"), "execution:alpaca:paper-primary"
            )

    def test_the_pipeline_default_agrees_with_the_workers(self):
        """resolve_execution_scope is what ExecutionPipeline falls back to,
        so a drift quarantine has to block the WebUI path too."""
        env = {
            "AUTONOMOUS_ACCOUNT_KEY": "paper-primary",
            "EXECUTION_QUARANTINE_SCOPE": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(
                resolve_execution_scope("alpaca", configured=None),
                quarantine_scope_from_env("alpaca"),
            )
            self.assertEqual(
                resolve_execution_scope("alpaca", configured=""),
                "execution:alpaca:paper-primary",
            )

    def test_configured_scope_from_config_wins_over_the_environment(self):
        env = {"AUTONOMOUS_ACCOUNT_KEY": "paper-primary"}
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(
                resolve_execution_scope("alpaca", configured=" execution:pinned "),
                "execution:pinned",
            )

    def test_an_explicit_scope_overrides_the_account_key(self):
        env = {
            "AUTONOMOUS_ACCOUNT_KEY": "paper-primary",
            "EXECUTION_QUARANTINE_SCOPE": "execution:alpaca:shared",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(
                quarantine_scope_from_env("alpaca"), "execution:alpaca:shared"
            )


class EnvFlagTests(unittest.TestCase):
    def test_recognised_truthy_and_falsy_spellings(self):
        from tradingagents.orchestration.autonomous_worker import _enabled

        for value in ("1", "true", "TRUE", " yes ", "on"):
            with mock.patch.dict(os.environ, {"FLAG": value}, clear=False):
                self.assertTrue(_enabled("FLAG"), value)

        for value in ("0", "false", "no", "off", ""):
            with mock.patch.dict(os.environ, {"FLAG": value}, clear=False):
                self.assertFalse(_enabled("FLAG"), value)

    def test_absent_flag_is_off(self):
        from tradingagents.orchestration.autonomous_worker import _enabled

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FLAG", None)
            self.assertFalse(_enabled("FLAG"))


class SchedulerStartupTests(unittest.TestCase):
    def test_refuses_to_start_unless_explicitly_enabled(self):
        """Autonomous trading must never begin by default."""
        from tradingagents.orchestration.autonomous_worker import (
            build_scheduler_from_env,
        )

        with mock.patch.dict(os.environ, {"AUTONOMOUS_ENABLED": "false"}, clear=False):
            with self.assertRaises(ValueError) as raised:
                build_scheduler_from_env()

        self.assertIn("AUTONOMOUS_ENABLED", str(raised.exception))

    def test_requires_postgres_and_releases_the_engine_when_it_is_missing(self):
        """Startup failures must not leak the connection pool."""
        from tradingagents.orchestration import autonomous_worker

        closed = []
        runtime = SimpleNamespace(
            unit_of_work_factory=None, close=lambda: closed.append(True)
        )

        env = {"AUTONOMOUS_ENABLED": "true", "PERSISTENCE_BACKEND": "local"}
        with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(
            autonomous_worker, "build_persistence_runtime", lambda _config: runtime
        ):
            with self.assertRaises(ValueError) as raised:
                autonomous_worker.build_scheduler_from_env()

        self.assertIn("PERSISTENCE_BACKEND=postgres", str(raised.exception))
        self.assertEqual(closed, [True])


class MainTests(unittest.TestCase):
    def test_once_runs_a_single_cycle_and_closes(self):
        from tradingagents.orchestration import autonomous_worker

        scheduler = mock.Mock()
        scheduler.run_once.return_value = {"analyzed": 0}
        closed = []

        with mock.patch.object(
            autonomous_worker,
            "build_scheduler_from_env",
            lambda: (scheduler, lambda: closed.append(True)),
        ), mock.patch("sys.argv", ["tradingagents-autonomous-worker", "--once"]):
            autonomous_worker.main()

        scheduler.run_once.assert_called_once_with()
        scheduler.run_forever.assert_not_called()
        self.assertEqual(closed, [True])

    def test_default_run_loops_on_the_configured_interval(self):
        from tradingagents.orchestration import autonomous_worker

        scheduler = mock.Mock()
        closed = []

        with mock.patch.object(
            autonomous_worker,
            "build_scheduler_from_env",
            lambda: (scheduler, lambda: closed.append(True)),
        ), mock.patch(
            "sys.argv",
            ["tradingagents-autonomous-worker", "--interval-seconds", "42"],
        ):
            autonomous_worker.main()

        scheduler.run_forever.assert_called_once_with(interval_seconds=42.0)
        self.assertEqual(closed, [True])

    def test_the_engine_is_released_when_a_cycle_raises(self):
        from tradingagents.orchestration import autonomous_worker

        scheduler = mock.Mock()
        scheduler.run_once.side_effect = RuntimeError("broker unreachable")
        closed = []

        with mock.patch.object(
            autonomous_worker,
            "build_scheduler_from_env",
            lambda: (scheduler, lambda: closed.append(True)),
        ), mock.patch("sys.argv", ["tradingagents-autonomous-worker", "--once"]):
            with self.assertRaises(RuntimeError):
                autonomous_worker.main()

        self.assertEqual(closed, [True])


if __name__ == "__main__":
    unittest.main()
