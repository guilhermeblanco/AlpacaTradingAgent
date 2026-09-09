"""Callbacks for persisted decision lifecycle exploration."""

from dash import Input, Output, State, html
import dash_bootstrap_components as dbc


def _persistence_runtime():
    from tradingagents.dataflows.config import get_config
    from tradingagents.persistence import build_persistence_runtime

    return build_persistence_runtime(get_config() or {})


def _event_row(event):
    tone = {
        "fill": "success",
        "outcome": "info",
        "order": "primary",
        "lifecycle": "secondary",
    }.get(event.category, "secondary")
    details = ", ".join(
        f"{key}: {value}"
        for key, value in event.details.items()
        if value is not None and not isinstance(value, (dict, list))
    )
    return html.Div(
        [
            html.Div(event.occurred_at.strftime("%Y-%m-%d %H:%M:%S UTC"), className="decision-event-time"),
            html.Div(
                [
                    dbc.Badge(event.category.upper(), color=tone, className="me-2"),
                    html.Strong(event.label),
                    html.Div(details, className="text-muted small mt-1") if details else None,
                ],
                className="decision-event-content",
            ),
        ],
        className="decision-event-row",
    )


def register_decision_explorer_callbacks(app):
    @app.callback(
        Output("decision-explorer-selection", "options"),
        Output("decision-explorer-selection", "value"),
        Input("decision-explorer-interval", "n_intervals"),
        Input("decision-explorer-refresh", "n_clicks"),
        Input("decision-explorer-symbol", "value"),
        Input("decision-explorer-status", "value"),
        State("decision-explorer-selection", "value"),
    )
    def load_decisions(_interval, _refresh, symbol, status, selected):
        runtime = _persistence_runtime()
        if runtime.unit_of_work_factory is None:
            return [], None
        try:
            with runtime.unit_of_work_factory() as uow:
                decisions = uow.decision_explorer.list_decisions(
                    limit=100,
                    symbol=symbol or None,
                    status=status or None,
                )
            options = [
                {
                    "label": f"{item.symbol} | {item.status.upper()} | {item.created_at:%Y-%m-%d %H:%M}",
                    "value": item.decision_id,
                }
                for item in decisions
            ]
            values = {option["value"] for option in options}
            return options, selected if selected in values else (options[0]["value"] if options else None)
        finally:
            runtime.close()

    @app.callback(
        Output("decision-explorer-detail", "children"),
        Input("decision-explorer-selection", "value"),
    )
    def load_decision_detail(decision_id):
        if not decision_id:
            return html.Div("No persisted decisions found", className="text-muted py-4")
        runtime = _persistence_runtime()
        if runtime.unit_of_work_factory is None:
            return dbc.Alert("Decision history requires PostgreSQL.", color="warning")
        try:
            with runtime.unit_of_work_factory() as uow:
                detail = uow.decision_explorer.get_decision(decision_id)
            summary = detail.summary
            header = html.Div(
                [
                    html.Div([html.Small("Symbol", className="text-muted"), html.Strong(summary.symbol)]),
                    html.Div([html.Small("State", className="text-muted"), dbc.Badge(summary.status.upper(), color="primary")]),
                    html.Div([html.Small("Broker", className="text-muted"), html.Strong(summary.broker or "-")]),
                    html.Div([html.Small("Orders", className="text-muted"), html.Strong(str(summary.order_count))]),
                    html.Div([html.Small("Filled", className="text-muted"), html.Strong(f"{summary.filled_quantity:g}")]),
                ],
                className="decision-summary-grid mb-3",
            )
            error = dbc.Alert(summary.error, color="danger") if summary.error else None
            return html.Div([header, error, *[_event_row(event) for event in detail.timeline]])
        except Exception as exc:
            return dbc.Alert(f"Unable to load decision: {exc}", color="danger")
        finally:
            runtime.close()
