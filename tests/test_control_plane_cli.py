"""Tests for the operations control-plane CLI.

This is how an operator pauses a worker without a deploy, and how a
supervisor decides whether a service is alive. `check` is the one that
matters most: its exit code gates restarts, so a stale heartbeat has to
exit non-zero and a healthy one has to exit clean.
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

from tradingagents.operations import control_plane
from tradingagents.operations.control_plane import (
    OperationalHealth,
    ServiceControl,
    ServiceHeartbeat,
    main,
)


def _heartbeat(service="reconciliation", *, status="healthy", stale=False):
    return ServiceHeartbeat(
        service=service,
        instance_id="worker-1",
        status=status,
        last_seen_at=datetime(2026, 9, 9, 14, 30, tzinfo=timezone.utc),
        stale=stale,
    )


def _health(**overrides):
    fields = {
        "observed_at": datetime(2026, 9, 9, 14, 30, tzinfo=timezone.utc),
        "controls": [ServiceControl(service="reconciliation")],
        "heartbeats": [_heartbeat()],
        "reconciliation_pending": 2,
        "outbox_pending": 1,
    }
    fields.update(overrides)
    return OperationalHealth(**fields)


class ControlPlaneCliFixture(unittest.TestCase):
    def _run(self, argv, *, health=None, backend="postgres", control=None):
        operations = mock.MagicMock()
        operations.health.return_value = health if health is not None else _health()
        operations.set_paused.return_value = control or ServiceControl(
            service="reconciliation", paused=True, reason="maintenance"
        )

        uow = mock.MagicMock()
        uow.__enter__ = lambda _self: SimpleNamespace(
            operations=operations, commit=lambda: None
        )
        uow.__exit__ = lambda *a: False

        runtime = mock.MagicMock()
        runtime.unit_of_work_factory = (
            (lambda: uow) if backend == "postgres" else None
        )

        out = io.StringIO()
        with mock.patch(
            "tradingagents.persistence.build_persistence_runtime", lambda config: runtime
        ):
            with mock.patch("sys.argv", ["control-plane", *argv]):
                with contextlib.redirect_stdout(out):
                    try:
                        main()
                        code = 0
                    except SystemExit as exit_code:
                        code = exit_code.code
        return code, out.getvalue(), operations, runtime


class StatusTests(ControlPlaneCliFixture):
    def test_status_prints_the_health_projection(self):
        code, output, operations, _runtime = self._run(["status"])

        self.assertEqual(code, 0)
        parsed = json.loads(output)
        self.assertEqual(parsed["reconciliation_pending"], 2)
        self.assertEqual(parsed["outbox_pending"], 1)
        operations.health.assert_called_once_with(stale_after_seconds=600)

    def test_the_staleness_threshold_is_configurable(self):
        _code, _output, operations, _runtime = self._run(
            ["status", "--stale-after-seconds", "30"]
        )

        operations.health.assert_called_once_with(stale_after_seconds=30.0)

    def test_the_connection_is_always_released(self):
        _code, _output, _operations, runtime = self._run(["status"])

        runtime.close.assert_called_once()


class CheckTests(ControlPlaneCliFixture):
    def test_a_healthy_service_exits_clean(self):
        code, _output, _operations, _runtime = self._run(
            ["check", "reconciliation"]
        )

        self.assertEqual(code, 0)

    def test_a_running_or_paused_service_also_counts_as_alive(self):
        for status in ("running", "paused"):
            code, _output, _operations, _runtime = self._run(
                ["check", "reconciliation"],
                health=_health(heartbeats=[_heartbeat(status=status)]),
            )

            self.assertEqual(code, 0, status)

    def test_a_stale_heartbeat_exits_nonzero(self):
        """This is what tells a supervisor to restart the worker."""
        code, _output, _operations, _runtime = self._run(
            ["check", "reconciliation"],
            health=_health(heartbeats=[_heartbeat(stale=True)]),
        )

        self.assertEqual(code, 1)

    def test_a_crashed_service_exits_nonzero(self):
        code, _output, _operations, _runtime = self._run(
            ["check", "reconciliation"],
            health=_health(heartbeats=[_heartbeat(status="error")]),
        )

        self.assertEqual(code, 1)

    def test_a_service_with_no_heartbeat_at_all_exits_nonzero(self):
        code, _output, _operations, _runtime = self._run(
            ["check", "reconciliation"], health=_health(heartbeats=[])
        )

        self.assertEqual(code, 1)

    def test_another_services_heartbeat_does_not_count(self):
        code, _output, _operations, _runtime = self._run(
            ["check", "reconciliation"],
            health=_health(heartbeats=[_heartbeat(service="evaluation")]),
        )

        self.assertEqual(code, 1)

    def test_the_service_name_is_matched_case_insensitively(self):
        code, _output, _operations, _runtime = self._run(
            ["check", "  Reconciliation  "]
        )

        self.assertEqual(code, 0)

    def test_the_connection_is_released_even_when_the_check_fails(self):
        _code, _output, _operations, runtime = self._run(
            ["check", "reconciliation"], health=_health(heartbeats=[])
        )

        runtime.close.assert_called_once()


class PauseResumeTests(ControlPlaneCliFixture):
    def test_pausing_records_the_reason_and_the_operator(self):
        code, output, operations, _runtime = self._run(
            ["pause", "reconciliation", "--reason", "maintenance",
             "--updated-by", "alice"]
        )

        self.assertEqual(code, 0)
        operations.set_paused.assert_called_once_with(
            "reconciliation", paused=True, reason="maintenance", updated_by="alice"
        )
        self.assertTrue(json.loads(output)["paused"])

    def test_resuming_clears_the_pause(self):
        _code, _output, operations, _runtime = self._run(
            ["resume", "reconciliation"],
            control=ServiceControl(service="reconciliation", paused=False),
        )

        self.assertIs(operations.set_paused.call_args.kwargs["paused"], False)

    def test_the_operator_defaults_to_the_shell_user(self):
        with mock.patch.dict("os.environ", {"USER": "bob"}):
            import importlib

            importlib.reload(control_plane)
            try:
                _code, _output, operations, _runtime = self._run(
                    ["pause", "reconciliation"]
                )
            finally:
                importlib.reload(control_plane)

        self.assertEqual(operations.set_paused.call_args.kwargs["updated_by"], "bob")


class BackendTests(ControlPlaneCliFixture):
    def test_the_control_plane_requires_postgres(self):
        """Pause state has to outlive the process that set it."""
        runtime = mock.MagicMock()
        runtime.unit_of_work_factory = None

        with mock.patch(
            "tradingagents.persistence.build_persistence_runtime", lambda config: runtime
        ):
            with mock.patch("sys.argv", ["control-plane", "status"]):
                with self.assertRaises(ValueError) as raised:
                    main()

        self.assertIn("requires PostgreSQL", str(raised.exception))
        runtime.close.assert_called_once()

    def test_a_command_is_required(self):
        with mock.patch("sys.argv", ["control-plane"]):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    main()


if __name__ == "__main__":
    unittest.main()
