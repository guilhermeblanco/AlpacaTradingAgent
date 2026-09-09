"""The Decision Tape: one decision, all seven stages, with its charts.

The panel is deliberately linear. A decision moves through the pipeline in
one direction, so the tape reads top to bottom in that order, and each
stage is either reached or visibly not.
"""

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import dcc, html

from tradingagents.workbench.tape import STAGES, StageState
from webui.config.constants import COLORS
from webui.config.tokens import status_badge, status_color

#: Stage and gate states share one vocabulary with agents and workers, so
#: the same condition looks the same wherever it is drawn. See
#: webui.config.tokens.
STATE_COLORS = {state.value: status_color(state.value) for state in StageState}
STATE_BADGES = {state.value: status_badge(state.value) for state in StageState}
GATE_STATUS_COLORS = {
    status: status_color(status)
    for status in ("passed", "clipped", "blocked", "skipped")
}

CHART_LAYOUT = {
    "template": "plotly_dark",
    "paper_bgcolor": COLORS["card"],
    "plot_bgcolor": COLORS["card"],
    "margin": {"l": 8, "r": 8, "t": 28, "b": 8},
    "font": {"color": COLORS["text"], "size": 11},
    "showlegend": False,
}


def empty_figure(message: str) -> go.Figure:
    figure = go.Figure()
    figure.update_layout(
        **CHART_LAYOUT,
        height=180,
        xaxis={"visible": False},
        yaxis={"visible": False},
        annotations=[
            {
                "text": message,
                "showarrow": False,
                "font": {"color": COLORS["pending"], "size": 12},
            }
        ],
    )
    return figure


def gate_waterfall_figure(steps) -> go.Figure:
    """Requested notional, every clip that reduced it, and what was sent."""
    if not steps:
        return empty_figure("No gate ledger recorded for this decision")

    measures, values, labels, colors = [], [], [], []
    for step in steps:
        kind = step["kind"]
        labels.append(step["label"])
        values.append(step["amount"])
        if kind in ("start", "end"):
            measures.append("absolute" if kind == "start" else "total")
            colors.append(COLORS["primary"] if kind == "start" else COLORS["completed"])
        else:
            measures.append("relative")
            colors.append(
                COLORS["error"] if kind == "blocked" else COLORS["in_progress"]
            )

    figure = go.Figure(
        go.Waterfall(
            orientation="v",
            measure=measures,
            x=labels,
            y=values,
            connector={"line": {"color": COLORS["border"]}},
            decreasing={"marker": {"color": COLORS["in_progress"]}},
            increasing={"marker": {"color": COLORS["primary"]}},
            totals={"marker": {"color": COLORS["completed"]}},
            text=[f"${abs(value):,.0f}" for value in values],
            textposition="outside",
        )
    )
    figure.update_layout(**CHART_LAYOUT, height=260, title="Size through the gates")
    figure.update_yaxes(gridcolor=COLORS["border"], tickprefix="$")
    return figure


def evidence_figure(bars) -> go.Figure:
    """The scoreboard dimensions the managers adjudicated on."""
    if not bars:
        return empty_figure("No scored evidence recorded for this decision")

    labels = [bar["label"] for bar in bars]
    values = [bar["value"] for bar in bars]
    palette = {
        "Bullish": COLORS["completed"],
        "Bearish": COLORS["error"],
        "Contradiction": COLORS["in_progress"],
    }
    figure = go.Figure(
        go.Bar(
            x=values,
            y=labels,
            orientation="h",
            marker={"color": [palette.get(label, COLORS["primary"]) for label in labels]},
            text=[f"{value:.2f}" for value in values],
            textposition="auto",
        )
    )
    figure.update_layout(**CHART_LAYOUT, height=240, title="Evidence scoreboard")
    figure.update_xaxes(range=[0, 1], gridcolor=COLORS["border"])
    figure.update_yaxes(autorange="reversed")
    return figure


def outcome_figure(outcomes) -> go.Figure:
    """Realized excess return at each resolved horizon."""
    if not outcomes:
        return empty_figure("No horizon has resolved yet")

    horizons = [item.get("horizon", "") for item in outcomes]
    excess = [float(item.get("excess_return_pct", 0.0) or 0.0) for item in outcomes]
    figure = go.Figure(
        go.Bar(
            x=horizons,
            y=excess,
            marker={
                "color": [
                    COLORS["completed"] if value >= 0 else COLORS["error"]
                    for value in excess
                ]
            },
            text=[f"{value:+.2f}%" for value in excess],
            textposition="outside",
        )
    )
    figure.update_layout(
        **CHART_LAYOUT, height=220, title="Excess return vs benchmark"
    )
    figure.update_yaxes(gridcolor=COLORS["border"], ticksuffix="%", zerolinecolor=COLORS["border"])
    return figure


def stage_rail(stages, active: str = "") -> html.Div:
    """The seven stages as a horizontal progress rail."""
    nodes = []
    for index, stage in enumerate(stages):
        color = STATE_COLORS.get(stage.state.value, COLORS["pending"])
        nodes.append(
            html.Div(
                [
                    html.Div(
                        className="workbench-rail-dot",
                        style={
                            "backgroundColor": color,
                            "boxShadow": (
                                f"0 0 0 3px {color}33"
                                if stage.key == active
                                else "none"
                            ),
                        },
                    ),
                    html.Div(stage.label, className="workbench-rail-label"),
                ],
                className="workbench-rail-node",
            )
        )
        if index < len(stages) - 1:
            nodes.append(html.Div(className="workbench-rail-link"))
    return html.Div(nodes, className="workbench-rail")


def create_workbench_panel():
    """The tape, its selector, and the charts that break it down."""
    return html.Section(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.H3("Decision tape", className="mb-0"),
                            html.Small(
                                "Everything one decision was made of, in the order "
                                "the machine made it.",
                                className="text-muted",
                            ),
                        ]
                    ),
                    dbc.Button(
                        html.I(className="fas fa-rotate"),
                        id="workbench-refresh",
                        color="outline-secondary",
                        size="sm",
                        title="Reload decisions",
                    ),
                ],
                className="d-flex justify-content-between align-items-start mb-3",
            ),
            dbc.Row(
                [
                    dbc.Col(
                        dbc.Input(
                            id="workbench-symbol",
                            placeholder="Filter symbol",
                            debounce=True,
                        ),
                        md=3,
                    ),
                    dbc.Col(
                        dcc.Dropdown(
                            id="workbench-selection",
                            placeholder="Select a decision",
                            clearable=False,
                            className="workbench-dropdown",
                        ),
                        md=9,
                    ),
                ],
                className="g-2 mb-3",
            ),
            dcc.Interval(id="workbench-interval", interval=30_000),
            html.Div(id="workbench-rail", className="mb-3"),
            dbc.Row(
                [
                    dbc.Col(
                        dcc.Graph(
                            id="workbench-gate-waterfall",
                            config={"displayModeBar": False},
                        ),
                        lg=6,
                    ),
                    dbc.Col(
                        dcc.Graph(
                            id="workbench-evidence",
                            config={"displayModeBar": False},
                        ),
                        lg=3,
                    ),
                    dbc.Col(
                        dcc.Graph(
                            id="workbench-outcomes",
                            config={"displayModeBar": False},
                        ),
                        lg=3,
                    ),
                ],
                className="g-2 mb-3",
            ),
            dbc.Card(
                dbc.CardBody(
                    [
                        html.Div(
                            [
                                html.Strong("What would this do now?"),
                                html.Small(
                                    " Every gate re-evaluated against live "
                                    "account state. Nothing reaches a broker.",
                                    className="text-muted",
                                ),
                            ],
                            className="mb-2",
                        ),
                        dbc.Row(
                            [
                                dbc.Col(
                                    dbc.InputGroup(
                                        [
                                            dbc.InputGroupText("$"),
                                            dbc.Input(
                                                id="workbench-preview-notional",
                                                type="number",
                                                min=0,
                                                step=100,
                                                placeholder="Size to test",
                                            ),
                                        ]
                                    ),
                                    md=4,
                                ),
                                dbc.Col(
                                    dbc.Button(
                                        "Preview",
                                        id="workbench-preview",
                                        color="primary",
                                        outline=True,
                                        size="sm",
                                    ),
                                    md="auto",
                                ),
                            ],
                            className="g-2 align-items-center",
                        ),
                        html.Div(id="workbench-preview-result", className="mt-2"),
                    ]
                ),
                className="workbench-stage-card mb-3",
            ),
            html.Div(id="workbench-tape"),
        ],
        className="workbench-panel",
    )
