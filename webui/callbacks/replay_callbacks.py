"""Callbacks for variant replay and the promotion gate.

Replay is the expensive one: it re-runs the analysts, so it is started
deliberately, runs off the request thread, and reports progress. The
promotion gate reads what those replays eventually resolve into.
"""

import dash_bootstrap_components as dbc
from dash import Input, Output, State, html, no_update

from tradingagents.workbench.promotion_view import build_promotion_view, scorecard_rows
from tradingagents.workbench.replay import (
    ReplayRefused,
    ReplayRequest,
    get_replay_jobs,
    variants_from_env,
)
from webui.utils.persistence import get_persistence_runtime
from webui.utils.state import app_state

STATUS_COLORS = {
    "queued": "secondary",
    "running": "warning",
    "done": "success",
    "failed": "danger",
}

PROMOTION_COLORS = {
    "eligible": "success",
    "rejected": "danger",
    "insufficient_data": "secondary",
}


def variant_options():
    return [
        {
            "label": (
                f"{variant.experiment_id}"
                + (" (executes)" if variant.execution_eligible else " (shadow)")
            ),
            "value": variant.experiment_id,
        }
        for variant in variants_from_env()
    ]


def variant_by_id(experiment_id):
    return next(
        (
            variant
            for variant in variants_from_env()
            if variant.experiment_id == experiment_id
        ),
        None,
    )


def experiment_options():
    """Experiments that have recorded at least one episode."""
    runtime = get_persistence_runtime()
    if runtime.unit_of_work_factory is None:
        return []
    with runtime.unit_of_work_factory() as uow:
        return [
            {"label": item, "value": item} for item in uow.evaluation.experiment_ids()
        ]


def horizon_options():
    runtime = get_persistence_runtime()
    if runtime.unit_of_work_factory is None:
        return []
    with runtime.unit_of_work_factory() as uow:
        horizons = sorted({row.horizon for row in uow.evaluation.outcomes()})
    return [{"label": item, "value": item} for item in horizons]


def render_jobs(jobs):
    """Recent replays, newest first."""
    if not jobs:
        return html.Div(
            "No replay has been run in this session.", className="text-muted small"
        )
    rows = []
    for job in jobs:
        outcome = job.outcome
        detail = []
        if outcome is not None:
            if outcome.action:
                detail.append(f"decided {outcome.action}")
            if outcome.episode_recorded:
                detail.append("shadow episode recorded")
            if outcome.note:
                detail.append(outcome.note)
            if not outcome.point_in_time_verified:
                detail.append(
                    "sources were not fully date-bounded — a same-day replay "
                    "searches live and can see news published since the "
                    "original decision"
                )
        if job.error:
            detail.append(job.error)
        rows.append(
            html.Div(
                [
                    dbc.Badge(
                        job.status.upper(),
                        color=STATUS_COLORS.get(job.status, "secondary"),
                        className="me-2",
                    ),
                    html.Code(job.request.experiment_id, className="me-2 small"),
                    html.Span(job.request.symbol, className="me-2"),
                    html.Span(" · ".join(detail), className="small text-muted"),
                ],
                className="mb-1",
            )
        )
    return html.Div(rows)


def render_promotion(view):
    """The verdict, the two scorecards, and how much of it was replayed."""
    if view is None:
        return dbc.Alert(
            "The promotion gate reads from PostgreSQL.",
            color="warning",
            className="py-2 mb-0",
        )
    if not view.available:
        return html.Div(view.unavailable, className="text-muted small")

    decision = view.decision
    status = decision.status.value
    header = html.Div(
        [
            html.Strong(f"{view.challenger} vs {view.champion}", className="me-2"),
            dbc.Badge(
                status.replace("_", " ").upper(),
                color=PROMOTION_COLORS.get(status, "secondary"),
                className="me-2",
            ),
            html.Span(
                f"uplift {decision.uplift_pct:+.2f}% "
                f"(lower bound {decision.uplift_lcb_pct:+.2f}%)",
                className="small text-muted",
            ),
        ],
        className="mb-2",
    )
    table = dbc.Table(
        [
            html.Thead(
                html.Tr(
                    [html.Th(""), html.Th(view.challenger), html.Th(view.champion)]
                )
            ),
            html.Tbody(
                [
                    html.Tr(
                        [
                            html.Td(row["label"]),
                            html.Td(
                                html.Strong(row["challenger"])
                                if row["better"]
                                else row["challenger"]
                            ),
                            html.Td(row["champion"]),
                        ]
                    )
                    for row in scorecard_rows(view)
                ]
            ),
        ],
        bordered=False,
        size="sm",
        responsive=True,
    )
    reasons = (
        html.Ul(
            [html.Li(reason, className="small") for reason in decision.reasons],
            className="mb-2",
        )
        if decision.reasons
        else None
    )
    notes = (
        html.Div(
            [html.Div(note, className="small text-muted") for note in view.notes],
        )
        if view.notes
        else None
    )
    return html.Div([header, table, reasons, notes])


def load_promotion(challenger, champion, horizon, include_replays):
    runtime = get_persistence_runtime()
    if runtime.unit_of_work_factory is None:
        return None
    with runtime.unit_of_work_factory() as uow:
        return build_promotion_view(
            uow,
            challenger=challenger,
            champion=champion,
            horizon=horizon,
            include_replays=include_replays,
        )


def register_replay_callbacks(app):
    @app.callback(
        Output("workbench-variant", "options"),
        Output("workbench-variant", "value"),
        Input("workbench-interval", "n_intervals"),
        State("workbench-variant", "value"),
    )
    def load_variants(_intervals, selected):
        options = variant_options()
        values = {option["value"] for option in options}
        return options, (
            selected if selected in values else (options[0]["value"] if options else None)
        )

    @app.callback(
        Output("workbench-challenger", "options"),
        Output("workbench-challenger", "value"),
        Output("workbench-champion", "options"),
        Output("workbench-champion", "value"),
        Output("workbench-horizon", "options"),
        Output("workbench-horizon", "value"),
        Input("workbench-interval", "n_intervals"),
        Input("workbench-replay-status", "children"),
        State("workbench-challenger", "value"),
        State("workbench-champion", "value"),
        State("workbench-horizon", "value"),
    )
    def load_comparison_filters(_intervals, _status, challenger, champion, horizon):
        try:
            experiments = experiment_options()
            horizons = horizon_options()
        except Exception:
            return [], None, [], None, [], None

        def keep(value, options, fallback_index=0):
            values = [option["value"] for option in options]
            if value in values:
                return value
            return values[fallback_index] if len(values) > fallback_index else None

        return (
            experiments,
            keep(challenger, experiments, min(1, max(0, len(experiments) - 1))),
            experiments,
            keep(champion, experiments),
            horizons,
            keep(horizon, horizons),
        )

    @app.callback(
        Output("workbench-replay-status", "children"),
        Input("workbench-replay", "n_clicks"),
        Input("workbench-replay-interval", "n_intervals"),
        State("workbench-selection", "value"),
        State("workbench-variant", "value"),
    )
    def replay(n_clicks, _intervals, decision_id, experiment_id):
        """Start a replay on click; otherwise just report progress."""
        from dash import ctx

        jobs = get_replay_jobs()
        triggered = getattr(ctx, "triggered_id", None)
        if triggered != "workbench-replay" or not n_clicks:
            return render_jobs(jobs.recent())

        if not decision_id or not experiment_id:
            return dbc.Alert(
                "Pick a decision and a variant first.",
                color="warning",
                className="py-2",
            )
        request = build_request(decision_id, experiment_id)
        if isinstance(request, str):
            return dbc.Alert(request, color="warning", className="py-2")
        try:
            jobs.submit(request, analysis_running=bool(app_state.analysis_running))
        except ReplayRefused as exc:
            return dbc.Alert(str(exc), color="warning", className="py-2")
        return render_jobs(jobs.recent())

    @app.callback(
        Output("workbench-promotion", "children"),
        Input("workbench-challenger", "value"),
        Input("workbench-champion", "value"),
        Input("workbench-horizon", "value"),
        Input("workbench-include-replays", "value"),
        Input("workbench-replay-status", "children"),
    )
    def show_promotion(challenger, champion, horizon, include_replays, _status):
        try:
            view = load_promotion(
                challenger, champion, horizon, bool(include_replays)
            )
        except Exception as exc:
            return dbc.Alert(
                f"Unable to assess promotion: {exc}", color="danger", className="py-2"
            )
        return render_promotion(view)


def build_request(decision_id, experiment_id):
    """A replay request from a recorded decision, or a reason it cannot be made."""
    from webui.callbacks.workbench_callbacks import load_tape

    variant = variant_by_id(experiment_id)
    if variant is None:
        return f"No variant named {experiment_id} is configured."
    try:
        tape = load_tape(decision_id)
    except Exception as exc:
        return f"Unable to load decision: {exc}"
    if tape is None:
        return "Replay requires PostgreSQL."
    trade_date = tape.trade_date or (tape.analysis.get("trade_date") if tape.analysis else None)
    if not trade_date:
        return (
            "That decision has no recorded trade date, so a replay would not "
            "know which day to analyse."
        )
    return ReplayRequest(
        origin_decision_id=tape.decision_id,
        symbol=tape.symbol,
        trade_date=str(trade_date),
        experiment_id=variant.experiment_id,
        config_overrides=dict(variant.config_overrides or {}),
        decision_at=tape.created_at,
    )
