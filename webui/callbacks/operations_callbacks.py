"""Callbacks for the operational trading cockpit."""

from __future__ import annotations

from dash import Input, Output, ctx, html
import dash_bootstrap_components as dbc


def _metric(label, value, tone="neutral"):
    return html.Div(
        [html.Small(label, className="text-muted"), html.Div(value, className="operations-metric-value")],
        className=f"operations-metric operations-metric-{tone}",
    )


def _workers_table(heartbeats):
    if not heartbeats:
        return html.Div("No worker heartbeat recorded", className="text-muted small")
    return dbc.Table(
        [
            html.Thead(html.Tr([html.Th("Service"), html.Th("Status"), html.Th("Last seen")])),
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
    if not controls:
        return html.Div("No paused services", className="text-muted small")
    return dbc.Table(
        [
            html.Thead(html.Tr([html.Th("Scope"), html.Th("State"), html.Th("Reason")])),
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
    )
    def refresh_operations(_interval, _refresh, _pause, _resume):
        from tradingagents.dataflows.config import get_config
        from tradingagents.persistence import build_persistence_runtime

        config = get_config() or {}
        runtime = build_persistence_runtime(config)
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
                if ctx.triggered_id == "operations-pause-automation":
                    uow.operations.set_paused(
                        "autonomous-worker",
                        paused=True,
                        reason="paused from WebUI",
                        updated_by="webui",
                    )
                    action_status = dbc.Alert("Automation paused.", color="warning", className="py-2")
                elif ctx.triggered_id == "operations-resume-automation":
                    uow.operations.set_paused(
                        "autonomous-worker", paused=False, updated_by="webui"
                    )
                    action_status = dbc.Alert("Automation resumed.", color="success", className="py-2")
                health = uow.operations.health(stale_after_seconds=120)
                uow.commit()
            try:
                from tradingagents.broker import get_execution_broker_runtime

                broker_snapshot = get_execution_broker_runtime(config).snapshot_provider.get_portfolio_snapshot()
                broker_metrics = [
                    _metric("Equity", f"${broker_snapshot.account.equity:,.0f}"),
                    _metric("Cash", f"${broker_snapshot.account.cash:,.0f}"),
                    _metric("Gross exposure", f"${broker_snapshot.gross_exposure:,.0f}"),
                ]
            except Exception:
                broker_metrics = [_metric("Broker", "UNAVAILABLE", "danger")]
            metrics = broker_metrics + [
                _metric("Reconciliation queue", health.reconciliation_pending, "warning" if health.reconciliation_pending else "neutral"),
                _metric("Outbox pending", health.outbox_pending),
                _metric("Dead letters", health.outbox_dead_lettered, "danger" if health.outbox_dead_lettered else "neutral"),
                _metric("Active executions", health.active_executions),
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
        finally:
            runtime.close()
