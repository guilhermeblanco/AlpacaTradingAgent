"""The pipeline board: every decision in flight, grouped by the stage it reached.

The status table showed which agent was running for the symbols this
process happened to be analysing. The board shows every decision — live
ones from the running analysis and finished ones from the database — as
cards in the column of the stage they reached, each labelled with the gate
holding it. That is the "flowing machine" made literal.
"""

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import dcc, html

from tradingagents.workbench.tape import STAGES, StageState
from webui.components.workbench import CHART_LAYOUT, STATE_COLORS, empty_figure
from webui.config.constants import COLORS


def stage_distribution_figure(counts) -> go.Figure:
    """How many decisions sit at each stage — where the machine backs up."""
    if not any(counts.values()):
        return empty_figure("No decisions recorded yet")

    labels = [label for _key, label in STAGES]
    values = [counts.get(key, 0) for key, _label in STAGES]
    figure = go.Figure(
        go.Bar(
            x=labels,
            y=values,
            marker={"color": COLORS["primary"]},
            text=[str(value) if value else "" for value in values],
            textposition="outside",
        )
    )
    figure.update_layout(**CHART_LAYOUT, height=200, title="Decisions by stage")
    figure.update_yaxes(gridcolor=COLORS["border"], rangemode="tozero")
    return figure


def halt_breakdown_figure(counts) -> go.Figure:
    """Which gate stops decisions most often."""
    if not counts:
        return empty_figure("Nothing has been stopped")

    ordered = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    figure = go.Figure(
        go.Bar(
            x=[value for _label, value in ordered],
            y=[label for label, _value in ordered],
            orientation="h",
            marker={"color": COLORS["error"]},
            text=[str(value) for _label, value in ordered],
            textposition="auto",
        )
    )
    figure.update_layout(**CHART_LAYOUT, height=200, title="Stopped by")
    figure.update_xaxes(gridcolor=COLORS["border"], rangemode="tozero", dtick=1)
    figure.update_yaxes(autorange="reversed")
    return figure


def throughput_figure(series) -> go.Figure:
    """Decisions started per hour over the recent window."""
    if not series:
        return empty_figure("No throughput yet")

    figure = go.Figure(
        go.Scatter(
            x=[point["at"] for point in series],
            y=[point["count"] for point in series],
            mode="lines+markers",
            line={"color": COLORS["secondary"], "width": 2},
            fill="tozeroy",
            fillcolor="rgba(16, 185, 129, 0.10)",
        )
    )
    figure.update_layout(**CHART_LAYOUT, height=200, title="Decisions per hour")
    figure.update_yaxes(gridcolor=COLORS["border"], rangemode="tozero", dtick=1)
    figure.update_xaxes(gridcolor=COLORS["border"])
    return figure


def board_card(card, selected=None):
    """One decision as a card: symbol, state, and what is holding it.

    Clicking opens it on the tape. A live card has no decision to open yet,
    so it says so instead of looking clickable and doing nothing.
    """
    stage = card["stage_state"]
    accent = STATE_COLORS.get(stage, COLORS["pending"])
    children = [
        html.Div(
            [
                html.Span(card["symbol"], className="board-card-symbol"),
                dbc.Badge(
                    card["badge"],
                    color=card["badge_color"],
                    className="board-card-badge",
                ),
            ],
            className="d-flex justify-content-between align-items-center",
        ),
        html.Div(card["headline"], className="board-card-headline"),
    ]
    if card.get("hint"):
        children.append(html.Div(card["hint"], className="board-card-hint"))

    classes = ["board-card"]
    if card.get("live"):
        classes.append("board-card-live")
    if selected and card["id"] == selected:
        classes.append("board-card-active")
    return html.Div(
        children,
        className=" ".join(classes),
        style={"borderLeftColor": accent},
        id={"type": "board-card", "decision": card["id"]},
        n_clicks=0,
        title=(
            "Still running — it has no decision id until the risk manager "
            "produces an intent."
            if card.get("live")
            else "Open on the decision tape"
        ),
    )


def board_column(label, cards, selected=None):
    return html.Div(
        [
            html.Div(
                [
                    html.Span(label, className="board-column-title"),
                    html.Span(str(len(cards)), className="board-column-count"),
                ],
                className="board-column-header",
            ),
            html.Div(
                [board_card(card, selected) for card in cards]
                or [html.Div("—", className="board-column-empty")],
                className="board-column-body",
            ),
        ],
        className="board-column",
    )


def create_pipeline_board():
    return html.Section(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.H3("Pipeline", className="mb-0"),
                            html.Small(
                                "Every decision in flight, in the column of the "
                                "stage it reached.",
                                className="text-muted",
                            ),
                        ]
                    ),
                    dbc.Button(
                        html.I(className="fas fa-rotate"),
                        id="board-refresh",
                        color="outline-secondary",
                        size="sm",
                        title="Reload the board",
                    ),
                ],
                className="d-flex justify-content-between align-items-start mb-3",
            ),
            dcc.Interval(id="board-interval", interval=8_000),
            dcc.Store(id="board-scroll"),
            html.Div(id="board-note", className="mb-2"),
            html.Div(id="pipeline-board", className="board-columns mb-3"),
            dbc.Row(
                [
                    dbc.Col(
                        dcc.Graph(
                            id="board-stage-distribution",
                            config={"displayModeBar": False},
                        ),
                        lg=5,
                    ),
                    dbc.Col(
                        dcc.Graph(
                            id="board-halts", config={"displayModeBar": False}
                        ),
                        lg=3,
                    ),
                    dbc.Col(
                        dcc.Graph(
                            id="board-throughput", config={"displayModeBar": False}
                        ),
                        lg=4,
                    ),
                ],
                className="g-2",
            ),
        ],
        className="workbench-panel",
    )
