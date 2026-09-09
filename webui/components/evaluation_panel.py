"""Realized-outcome scorecards and experiment promotion gates.

The evaluation ledger records what each decision was actually worth once
its horizon elapsed, and the promotion policy decides whether a challenger
has earned the champion slot. Both were backend-only.
"""

import dash_bootstrap_components as dbc
from dash import dcc, html


def create_evaluation_panel():
    return dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        html.Div(
                            [
                                html.H4("Decision Quality", className="mb-1"),
                                html.Div(
                                    "Realized returns against the benchmark, per "
                                    "horizon, once each decision's window closed.",
                                    className="text-muted small",
                                ),
                            ]
                        ),
                        dbc.Button(
                            html.I(className="fas fa-rotate"),
                            id="evaluation-refresh",
                            color="outline-secondary",
                            size="sm",
                            title="Refresh evaluation outcomes",
                        ),
                    ],
                    className="d-flex justify-content-between align-items-start mb-3",
                ),
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                dbc.Label("Horizon", className="small mb-1"),
                                dbc.Select(id="evaluation-horizon", size="sm"),
                            ],
                            md=3,
                            xs=6,
                        ),
                        dbc.Col(
                            [
                                dbc.Label("Challenger", className="small mb-1"),
                                dbc.Select(id="evaluation-challenger", size="sm"),
                            ],
                            md=3,
                            xs=6,
                        ),
                        dbc.Col(
                            [
                                dbc.Label("Champion", className="small mb-1"),
                                dbc.Select(id="evaluation-champion", size="sm"),
                            ],
                            md=3,
                            xs=6,
                        ),
                        dbc.Col(
                            [
                                dbc.Label("Min outcomes", className="small mb-1"),
                                dbc.Input(
                                    id="evaluation-min-outcomes",
                                    type="number",
                                    min=2,
                                    step=1,
                                    value=30,
                                    size="sm",
                                ),
                            ],
                            md=3,
                            xs=6,
                        ),
                    ],
                    className="g-2 mb-3",
                ),
                html.Div(id="evaluation-summary", className="evaluation-metric-grid"),
                html.Div(id="evaluation-promotion", className="mt-3"),
                dcc.Interval(
                    id="evaluation-refresh-interval",
                    interval=60_000,
                    n_intervals=0,
                ),
            ]
        ),
        className="mb-4",
    )
