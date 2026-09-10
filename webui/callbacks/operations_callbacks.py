"""Callbacks for the operational trading cockpit."""

from __future__ import annotations

from dash import ALL, Input, Output, ctx, html
import dash_bootstrap_components as dbc

from webui.utils.persistence import get_persistence_runtime


AUTOMATION_SERVICE = "autonomous-worker"


def _metric(label, value, tone="neutral"):
    return html.Div(
        [html.Small(label, className="text-muted"), html.Div(value, className="operations-metric-value")],
        className=f"operations-metric operations-metric-{tone}",
    )


def _lag_label(seconds):
    seconds = float(seconds or 0)
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"


def _workers_table(heartbeats):
    if not heartbeats:
        return html.Div("No worker heartbeat recorded", className="text-muted small")
    return dbc.Table(
        [
            html.Thead(
                html.Tr(
                    [
                        html.Th("Service"), html.Th("Status"),
                        html.Th("Last seen"), html.Th(""),
                    ]
                )
            ),
            html.Tbody(
                [
                    html.Tr(
                        [
                            html.Td(item.service),
                            html.Td(
                                dbc.Badge(
                                    "STALE" if item.stale else item.status.upper(),
                                    color="danger" if item.stale else "success",
                                )
                            ),
                            html.Td(item.last_seen_at.strftime("%H:%M:%S UTC")),
                            html.Td(
                                dbc.Button(
                                    "Restart",
                                    id={
                                        "type": "restart-service",
                                        "service": item.service,
                                    },
                                    color="link",
                                    size="sm",
                                    className="p-0",
                                    title=(
                                        "Ask this worker to exit; the container "
                                        "runtime starts it again"
                                    ),
                                )
                            ),
                        ]
                    )
                    for item in heartbeats
                ]
            ),
        ],
        responsive=True,
        hover=True,
        size="sm",
        className="operations-table",
    )


def _controls_table(controls):
    """Every recorded control, with a resume action on the paused ones.

    Drift detection and reconciliation quarantine pause `execution:<scope>`
    automatically, so those scopes need an operator action here — otherwise
    clearing one requires the control-plane CLI.
    """
    if not controls:
        return html.Div("No service controls recorded", className="text-muted small")
    return dbc.Table(
        [
            html.Thead(
                html.Tr(
                    [html.Th("Scope"), html.Th("State"), html.Th("Reason"), html.Th("Action")]
                )
            ),
            html.Tbody(
                [
                    html.Tr(
                        [
                            html.Td(item.service),
                            html.Td(
                                dbc.Badge(
                                    "PAUSED" if item.paused else "ACTIVE",
                                    color="danger" if item.paused else "success",
                                )
                            ),
                            html.Td(item.reason or ""),
                            html.Td(
                                dbc.Button(
                                    "Resume",
                                    id={
                                        "type": "operations-resume-scope",
                                        "scope": item.service,
                                    },
                                    color="outline-success",
                                    size="sm",
                                )
                                if item.paused
                                else ""
                            ),
                        ]
                    )
                    for item in controls
                ]
            ),
        ],
        responsive=True,
        hover=True,
        size="sm",
        className="operations-table",
    )


def _apply_action(uow):
    """Run the control-plane action the click asked for, if any."""
    triggered = ctx.triggered_id
    clicks = ctx.triggered[0]["value"] if ctx.triggered else None

    if triggered == "operations-pause-automation" and clicks:
        uow.operations.set_paused(
            AUTOMATION_SERVICE,
            paused=True,
            reason="paused from WebUI",
            updated_by="webui",
        )
        return dbc.Alert("Automation paused.", color="warning", className="py-2")

    if triggered == "operations-resume-automation" and clicks:
        uow.operations.set_paused(
            AUTOMATION_SERVICE, paused=False, updated_by="webui"
        )
        return dbc.Alert("Automation resumed.", color="success", className="py-2")

    # A pattern-matching input also fires when the button list changes, so a
    # resume must be driven by an actual click rather than by re-rendering.
    if (
        isinstance(triggered, dict)
        and triggered.get("type") == "operations-resume-scope"
        and clicks
    ):
        scope = triggered.get("scope")
        uow.operations.set_paused(scope, paused=False, updated_by="webui")
        return dbc.Alert(f"Resumed {scope}.", color="success", className="py-2")

    return ""


def build_operations_view():
    """Apply the pending control action and project current operations state.

    Kept out of the Dash callback so it can be exercised directly.
    """
    from tradingagents.dataflows.config import get_config

    runtime = get_persistence_runtime()
    if runtime.unit_of_work_factory is None:
        return (
            "PostgreSQL not connected",
            [_metric("System", "SETUP REQUIRED", "warning")],
            dbc.Alert("Worker state requires PostgreSQL.", color="warning"),
            "",
            "",
        )
    action_status = ""
    try:
        with runtime.unit_of_work_factory() as uow:
            action_status = _apply_action(uow)
            # Per-service tolerances; see tradingagents/operations/services.py.
            health = uow.operations.health()
            uow.commit()
        try:
            from tradingagents.broker import get_execution_broker_runtime

            snapshot = get_execution_broker_runtime(
                get_config() or {}
            ).snapshot_provider.get_portfolio_snapshot()
            broker_metrics = [
                _metric("Equity", f"${snapshot.account.equity:,.0f}"),
                _metric("Cash", f"${snapshot.account.cash:,.0f}"),
                _metric("Gross exposure", f"${snapshot.gross_exposure:,.0f}"),
            ]
        except Exception:
            broker_metrics = [_metric("Broker", "UNAVAILABLE", "danger")]
        paused = [control for control in health.controls if control.paused]
        metrics = broker_metrics + [
            _metric(
                "Reconciliation queue",
                health.reconciliation_pending,
                "warning" if health.reconciliation_pending else "neutral",
            ),
            _metric(
                "Oldest reconciliation",
                _lag_label(health.reconciliation_oldest_lag_seconds),
                "warning"
                if health.reconciliation_oldest_lag_seconds > 300
                else "neutral",
            ),
            _metric("Outbox pending", health.outbox_pending),
            _metric(
                "Dead letters",
                health.outbox_dead_lettered,
                "danger" if health.outbox_dead_lettered else "neutral",
            ),
            _metric("Analyses in flight", health.analyses_in_flight),
            _metric("Active executions", health.active_executions),
            _metric("Reserved allocations", health.active_reservation_allocations),
            _metric("Paused scopes", len(paused), "danger" if paused else "neutral"),
        ]
        return (
            health.observed_at.strftime("Observed %Y-%m-%d %H:%M:%S UTC"),
            metrics,
            _workers_table(health.heartbeats),
            _controls_table(health.controls),
            action_status,
        )
    except Exception as exc:
        return (
            "Operations unavailable",
            [_metric("System", "UNAVAILABLE", "danger")],
            dbc.Alert(str(exc), color="danger"),
            "",
            action_status,
        )


def register_operations_callbacks(app):
    @app.callback(
        Output("operations-observed-at", "children"),
        Output("operations-metrics", "children"),
        Output("operations-workers", "children"),
        Output("operations-controls", "children"),
        Output("operations-action-status", "children"),
        Input("operations-refresh-interval", "n_intervals"),
        Input("operations-refresh", "n_clicks"),
        Input("operations-pause-automation", "n_clicks"),
        Input("operations-resume-automation", "n_clicks"),
        Input({"type": "operations-resume-scope", "scope": ALL}, "n_clicks"),
    )
    def refresh_operations(_interval, _refresh, _pause, _resume, _scope_clicks):
        return build_operations_view()


def register_restart_callbacks(app):
    """Ask a worker to restart.

    "Ask" is exact. The web process is in a different container with no
    way to signal a sibling, and the fix for that would be mounting the
    podman socket into the one process reachable from a browser with no
    authentication — a far worse trade than waiting a cycle. So the
    request is a row, the worker exits between units of work when it
    sees one, and `restart: unless-stopped` brings it back.
    """
    from dash import ALL, Input, Output, ctx, no_update

    @app.callback(
        Output("operations-restart-status", "children"),
        Input({"type": "restart-service", "service": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def restart(clicks):
        triggered = getattr(ctx, "triggered_id", None)
        if not isinstance(triggered, dict) or not any(clicks or []):
            # The table re-renders on an interval, which recreates every
            # button with n_clicks back at zero.
            return no_update

        service = triggered.get("service")
        runtime = get_persistence_runtime()
        if runtime.unit_of_work_factory is None:
            return dbc.Alert(
                "Restarting a worker needs PostgreSQL — the request is a row.",
                color="warning", className="py-2 mb-0",
            )
        try:
            with runtime.unit_of_work_factory() as uow:
                uow.operations.request_restart(service, actor="webui")
                uow.commit()
        except Exception as exc:
            return dbc.Alert(
                f"Unable to request a restart: {exc}",
                color="danger", className="py-2 mb-0",
            )
        return dbc.Alert(
            [
                html.Strong(f"{service} will restart. "),
                "It exits after its current unit of work; the container "
                "runtime starts it again.",
            ],
            color="info", className="py-2 mb-0",
        )

