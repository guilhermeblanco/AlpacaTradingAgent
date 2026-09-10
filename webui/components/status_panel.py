"""Session counters and the refresh-state readout.

The agent status table this panel used to own is now the pipeline board:
the board shows every decision across all seven stages, live and persisted,
rather than agent rows for whichever symbols this process is holding.
"""

import dash_bootstrap_components as dbc
from dash import html


def create_status_panel():
    """Per-session counters and the auto-refresh state."""
    return dbc.Card(
        dbc.CardBody(
            [
                html.H4("Session", className="mb-3"),
                html.Hr(),
                dbc.Row(
                    [
                        dbc.Col(
                            html.Div(id="tool-calls-text", children="🧰 Tool Calls: 0"),
                            width=4,
                        ),
                        dbc.Col(
                            html.Div(id="llm-calls-text", children="🤖 LLM Calls: 0"),
                            width=4,
                        ),
                        dbc.Col(
                            html.Div(
                                id="reports-text", children="📊 Generated Reports: 0"
                            ),
                            width=4,
                        ),
                    ]
                ),
                html.Div(
                    id="refresh-status",
                    children="⏸️ Updates paused until analysis starts",
                    className="text-secondary mt-2",
                ),
            ]
        ),
        className="mb-4",
    )
