"""Operational trading cockpit."""

import dash_bootstrap_components as dbc
from dash import dcc, html


def create_operations_panel():
    return html.Section(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.H3("Operations", className="mb-0"),
                            html.Small(id="operations-observed-at", className="text-muted"),
                        ]
                    ),
                    dbc.ButtonGroup(
                        [
                            dbc.Button(
                                [html.I(className="fas fa-pause me-2"), "Pause automation"],
                                id="operations-pause-automation",
                                color="warning",
                                size="sm",
                            ),
                            dbc.Button(
                                [html.I(className="fas fa-play me-2"), "Resume automation"],
                                id="operations-resume-automation",
                                color="outline-success",
                                size="sm",
                            ),
                            dbc.Button(
                                html.I(className="fas fa-rotate"),
                                id="operations-refresh",
                                color="outline-secondary",
                                size="sm",
                                title="Refresh operations",
                            ),
                        ]
                    ),
                ],
                className="operations-header d-flex justify-content-between align-items-center mb-3",
            ),
            html.Div(id="operations-action-status"),
            html.Div(id="operations-metrics", className="operations-metric-grid"),
            dbc.Row(
                [
                    dbc.Col(
                        [
                            html.H5("Workers", className="mt-3"),
                            html.Div(id="operations-workers"),
                        ],
                        lg=7,
                    ),
                    dbc.Col(
                        [
                            html.H5("Execution controls", className="mt-3"),
                            html.Div(id="operations-controls"),
                        ],
                        lg=5,
                    ),
                ],
                className="g-4",
            ),
            dcc.Interval(id="operations-refresh-interval", interval=15_000, n_intervals=0),
        ],
        className="operations-panel mb-4",
    )
