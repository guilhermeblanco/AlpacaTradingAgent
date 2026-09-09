import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

from tradingagents.operations.control_plane import (
    OperationalHealth,
    ServiceControl,
    ServiceHeartbeat,
)


def _health(controls=(), heartbeats=()):
    return OperationalHealth(
        observed_at=datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc),
        controls=list(controls),
        heartbeats=list(heartbeats),
        reconciliation_pending=3,
        reconciliation_oldest_lag_seconds=900.0,
        outbox_pending=2,
        outbox_dead_lettered=1,
        analyses_in_flight=4,
        active_reservation_allocations=5,
        active_executions=6,
    )


class RecordingOperations:
    def __init__(self, health):
        self._health = health
        self.calls = []

    def set_paused(self, service, **kwargs):
        self.calls.append((service, kwargs))

    def health(self, **_kwargs):
        return self._health


class Uow:
    def __init__(self, operations):
        self.operations = operations

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def commit(self):
        pass


def _build_view(*, triggered_id, triggered_value, operations):
    """Run the cockpit view builder with a simulated Dash context."""
    from webui.callbacks import operations_callbacks

    runtime = SimpleNamespace(unit_of_work_factory=lambda: Uow(operations))
    context = SimpleNamespace(
        triggered_id=triggered_id,
        triggered=[{"value": triggered_value}],
    )
    with mock.patch.object(
        operations_callbacks, "get_persistence_runtime", lambda: runtime
    ), mock.patch.object(operations_callbacks, "ctx", context):
        return operations_callbacks.build_operations_view()


def _register():
    import dash

    from webui.callbacks.operations_callbacks import register_operations_callbacks

    app = dash.Dash(__name__, suppress_callback_exceptions=True)
    register_operations_callbacks(app)
    return app


class OperationsCockpitWebUITests(unittest.TestCase):
    def test_panel_exposes_operational_controls(self):
        from webui.components.operations_panel import create_operations_panel

        rendered = str(create_operations_panel())
        for component_id in (
            "operations-metrics",
            "operations-workers",
            "operations-controls",
            "operations-pause-automation",
            "operations-resume-automation",
        ):
            self.assertIn(component_id, rendered)

    def test_callbacks_register(self):
        app = _register()
        outputs = " ".join(app.callback_map)
        self.assertIn("operations-metrics.children", outputs)
        self.assertIn("operations-action-status.children", outputs)

    def test_paused_scope_renders_a_resume_button(self):
        from webui.callbacks.operations_callbacks import _controls_table

        rendered = str(
            _controls_table(
                [
                    ServiceControl(
                        service="execution:alpaca:paper-primary",
                        paused=True,
                        reason="account-wide drift",
                    )
                ]
            )
        )
        self.assertIn("operations-resume-scope", rendered)
        self.assertIn("execution:alpaca:paper-primary", rendered)

    def test_active_scope_has_no_resume_button(self):
        from webui.callbacks.operations_callbacks import _controls_table

        rendered = str(
            _controls_table([ServiceControl(service="autonomous-worker", paused=False)])
        )
        self.assertNotIn("operations-resume-scope", rendered)

    def test_resume_click_clears_the_quarantined_scope(self):
        """Drift and reconciliation quarantine pause execution:<scope>, which
        the automation buttons never touched."""
        operations = RecordingOperations(_health())

        _build_view(
            triggered_id={
                "type": "operations-resume-scope",
                "scope": "execution:alpaca:paper-primary",
            },
            triggered_value=1,
            operations=operations,
        )

        self.assertEqual(
            operations.calls,
            [
                (
                    "execution:alpaca:paper-primary",
                    {"paused": False, "updated_by": "webui"},
                )
            ],
        )

    def test_rerendered_resume_button_does_not_resume_on_its_own(self):
        """A pattern-matching input also fires when the button list changes."""
        operations = RecordingOperations(_health())

        _build_view(
            triggered_id={
                "type": "operations-resume-scope",
                "scope": "execution:alpaca",
            },
            triggered_value=None,
            operations=operations,
        )

        self.assertEqual(operations.calls, [])

    def test_metrics_surface_every_health_field(self):
        operations = RecordingOperations(
            _health(
                controls=[ServiceControl(service="execution:alpaca", paused=True)],
                heartbeats=[
                    ServiceHeartbeat(
                        service="autonomous-worker",
                        instance_id="a",
                        status="running",
                        last_seen_at=datetime(2026, 9, 8, 11, 59, tzinfo=timezone.utc),
                    )
                ],
            )
        )

        _observed, metrics, _workers, _controls, _status = _build_view(
            triggered_id=None, triggered_value=None, operations=operations
        )

        rendered = str(metrics)
        for label in (
            "Reconciliation queue",
            "Oldest reconciliation",
            "Outbox pending",
            "Dead letters",
            "Analyses in flight",
            "Active executions",
            "Reserved allocations",
            "Paused scopes",
        ):
            self.assertIn(label, rendered)
        self.assertIn("15m", rendered)


if __name__ == "__main__":
    unittest.main()
