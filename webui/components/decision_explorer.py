"""Decision lifecycle explorer UI."""

import dash_bootstrap_components as dbc
from dash import dcc, html


def create_decision_explorer():
    return html.Section(
        [
            html.Div(
                [
                    html.H3("Decision history", className="mb-0"),
                    dbc.Button(
                        html.I(className="fas fa-rotate"),
                        id="decision-explorer-refresh",
                        color="outline-secondary",
                        size="sm",
                        title="Refresh decision history",
                    ),
                ],
                className="d-flex justify-content-between align-items-center mb-3",
            ),
            dbc.Row(
                [
                    dbc.Col(
                        dbc.Input(
                            id="decision-explorer-symbol",
                            placeholder="Filter symbol",
                            debounce=True,
                        ),
                        md=3,
                    ),
                    dbc.Col(
                        dbc.Select(
                            id="decision-explorer-status",
                            options=[
                                {"label": "All states", "value": ""},
                                *[
                                    {"label": value.title(), "value": value}
                                    for value in (
                                        "received",
                                        "validated",
                                        "planned",
                                        "submitted",
                                        "filled",
                                        "succeeded",
                                        "blocked",
                                        "failed",
                                    )
                                ],
                            ],
                            value="",
                        ),
                        md=3,
                    ),
                    dbc.Col(
                        dcc.Dropdown(
                            id="decision-explorer-selection",
                            placeholder="Select a decision",
                            clearable=False,
                            className="decision-explorer-dropdown",
                        ),
                        md=6,
                    ),
                ],
                className="g-2 mb-3",
            ),
            html.Div(id="decision-explorer-detail"),
            dcc.Interval(id="decision-explorer-interval", interval=30_000, n_intervals=0),
        ],
        className="decision-explorer mb-4",
    )
