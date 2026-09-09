"""Callbacks for the decision-quality and experiment-promotion panel."""

from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import Input, Output, State, html

from webui.utils.persistence import get_persistence_runtime


NO_DATA = html.Div("No resolved outcomes recorded yet", className="text-muted small")

_STATUS_TONE = {
    "eligible": "success",
    "rejected": "danger",
    "insufficient_data": "secondary",
}


def _metric(label, value, tone="neutral"):
    return html.Div(
        [
            html.Small(label, className="text-muted"),
            html.Div(value, className="evaluation-metric-value"),
        ],
        className=f"evaluation-metric evaluation-metric-{tone}",
    )


def _pct(value):
    return f"{value:+.2f}%"


def load_outcomes(experiment_id=None):
    """Recorded outcomes plus the experiment ids available to compare."""
    runtime = get_persistence_runtime()
    if runtime.unit_of_work_factory is None:
        return None, []
    with runtime.unit_of_work_factory() as uow:
        return (
            uow.evaluation.outcomes(experiment_id=experiment_id),
            uow.evaluation.experiment_ids(),
        )


def summarize(outcomes, horizon):
    """Scorecard tiles for one horizon."""
    from tradingagents.evaluation.reporting import summarize_outcomes

    rows = [row for row in outcomes if row.horizon == horizon]
    summary = summarize_outcomes(rows)
    if not summary["count"]:
        return NO_DATA
    excess = summary["mean_excess_return_pct"]
    return [
        _metric("Resolved decisions", summary["count"]),
        _metric(
            "Hit rate",
            f"{summary['hit_rate_pct']:.1f}%",
            "success" if summary["hit_rate_pct"] >= 50 else "warning",
        ),
        _metric(
            "Mean excess return",
            _pct(excess),
            "success" if excess > 0 else "danger",
        ),
        _metric("Mean asset return", _pct(summary["mean_asset_return_pct"])),
        _metric(
            "Estimated cost", f"{summary['total_estimated_cost_pct']:.2f}%", "warning"
        ),
    ]


def assess(
    challenger_outcomes,
    champion_outcomes,
    *,
    horizon,
    challenger,
    champion,
    min_outcomes,
):
    """Promotion verdict for a challenger against the champion.

    An outcome carries no experiment id of its own: the ledger resolves it
    through the episode that produced it, so each side is loaded separately.
    """
    from tradingagents.evaluation.promotion import PromotionPolicy, assess_promotion

    if not challenger or not champion:
        return html.Div(
            "Select a challenger and a champion to compare.",
            className="text-muted small",
        )
    if challenger == champion:
        return html.Div(
            "Challenger and champion must differ.", className="text-muted small"
        )

    def for_horizon(rows):
        return [row for row in rows or [] if row.horizon == horizon]

    try:
        decision = assess_promotion(
            for_horizon(challenger_outcomes),
            for_horizon(champion_outcomes),
            horizon=horizon,
            policy=PromotionPolicy(min_outcomes=max(2, int(min_outcomes or 30))),
        )
    except Exception as exc:
        return dbc.Alert(f"Unable to assess promotion: {exc}", color="danger")

    status = decision.status.value
    return html.Div(
        [
            html.Div(
                [
                    html.Strong(f"{challenger} vs {champion}", className="me-2"),
                    dbc.Badge(
                        status.replace("_", " ").upper(),
                        color=_STATUS_TONE.get(status, "secondary"),
                    ),
                ],
                className="mb-2",
            ),
            dbc.Table(
                [
                    html.Thead(
                        html.Tr(
                            [
                                html.Th("Variant"),
                                html.Th("Outcomes"),
                                html.Th("Hit rate"),
                                html.Th("Mean excess"),
                                html.Th("Excess LCB"),
                            ]
                        )
                    ),
                    html.Tbody(
                        [
                            html.Tr(
                                [
                                    html.Td(name),
                                    html.Td(card.count),
                                    html.Td(f"{card.hit_rate_pct:.1f}%"),
                                    html.Td(_pct(card.mean_excess_return_pct)),
                                    html.Td(_pct(card.excess_return_lcb_pct)),
                                ]
                            )
                            for name, card in (
                                (challenger, decision.challenger),
                                (champion, decision.champion),
                            )
                        ]
                    ),
                ],
                responsive=True,
                size="sm",
                hover=True,
            ),
            html.Div(
                f"Uplift {_pct(decision.uplift_pct)} "
                f"(lower confidence bound {_pct(decision.uplift_lcb_pct)})",
                className="small mb-2",
            ),
            # assess_promotion always explains itself, including on success.
            html.Ul(
                [html.Li(reason) for reason in decision.reasons],
                className="small text-muted mb-0",
            ),
        ]
    )


def register_evaluation_callbacks(app):
    @app.callback(
        Output("evaluation-horizon", "options"),
        Output("evaluation-horizon", "value"),
        Output("evaluation-challenger", "options"),
        Output("evaluation-challenger", "value"),
        Output("evaluation-champion", "options"),
        Output("evaluation-champion", "value"),
        Input("evaluation-refresh-interval", "n_intervals"),
        Input("evaluation-refresh", "n_clicks"),
        State("evaluation-horizon", "value"),
        State("evaluation-challenger", "value"),
        State("evaluation-champion", "value"),
    )
    def load_filters(_interval, _refresh, horizon, challenger, champion):
        outcomes, experiments = load_outcomes()
        if outcomes is None:
            return [], None, [], None, [], None
        horizons = sorted({row.horizon for row in outcomes})
        horizon_options = [{"label": item, "value": item} for item in horizons]
        experiment_options = [{"label": item, "value": item} for item in experiments]

        def keep(value, allowed, fallback_index=0):
            if value in allowed:
                return value
            return allowed[fallback_index] if len(allowed) > fallback_index else None

        return (
            horizon_options,
            keep(horizon, horizons),
            experiment_options,
            keep(challenger, experiments, min(1, max(0, len(experiments) - 1))),
            experiment_options,
            keep(champion, experiments),
        )

    @app.callback(
        Output("evaluation-summary", "children"),
        Output("evaluation-promotion", "children"),
        Input("evaluation-horizon", "value"),
        Input("evaluation-challenger", "value"),
        Input("evaluation-champion", "value"),
        Input("evaluation-min-outcomes", "value"),
        Input("evaluation-refresh", "n_clicks"),
    )
    def render_evaluation(horizon, challenger, champion, min_outcomes, _refresh):
        try:
            outcomes, _experiments = load_outcomes()
        except Exception as exc:
            return "", dbc.Alert(f"Unable to load outcomes: {exc}", color="danger")
        if outcomes is None:
            return (
                [_metric("Evaluation", "SETUP REQUIRED", "warning")],
                dbc.Alert("Decision quality requires PostgreSQL.", color="warning"),
            )
        if not horizon:
            return NO_DATA, ""
        try:
            challenger_outcomes = (
                load_outcomes(challenger)[0] if challenger else []
            )
            champion_outcomes = load_outcomes(champion)[0] if champion else []
        except Exception as exc:
            return summarize(outcomes, horizon), dbc.Alert(
                f"Unable to load experiment outcomes: {exc}", color="danger"
            )
        return (
            summarize(outcomes, horizon),
            assess(
                challenger_outcomes,
                champion_outcomes,
                horizon=horizon,
                challenger=challenger,
                champion=champion,
                min_outcomes=min_outcomes,
            ),
        )
