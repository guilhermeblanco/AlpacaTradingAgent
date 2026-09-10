"""Callbacks for the Decision Tape.

Reads only, and entirely from PostgreSQL: the tape has to show decisions
from autonomous cycles and past sessions, not just whatever this process
happens to be holding in memory.
"""

import dash_bootstrap_components as dbc
from dash import Input, Output, State, html

from tradingagents.workbench.tape import StageState
from webui.config.tokens import DEFAULT_THEME
from webui.components.workbench import (
    STATE_BADGES,
    GATE_STATUS_COLORS,
    empty_figure,
    evidence_figure,
    gate_waterfall_figure,
    outcome_figure,
    stage_rail,
)
from webui.utils.persistence import get_persistence_runtime

NEEDS_POSTGRES = "The decision tape reads from PostgreSQL. Configure DATABASE_URL to use it."


def load_board(symbol=None, limit=50):
    """Recent decisions as tapes, or None when there is no database."""
    runtime = get_persistence_runtime()
    if runtime.unit_of_work_factory is None:
        return None
    with runtime.unit_of_work_factory() as uow:
        return uow.workbench.board(limit=limit, symbol=symbol or None)


def load_tape(decision_id):
    runtime = get_persistence_runtime()
    if runtime.unit_of_work_factory is None:
        return None
    with runtime.unit_of_work_factory() as uow:
        return uow.workbench.tape(decision_id)


def decision_option(tape):
    """One dropdown entry. Shared with the board so a card and its option
    read the same."""
    stamp = tape.created_at.strftime("%Y-%m-%d %H:%M") if tape.created_at else "—"
    halted = tape.halted_at
    marker = f" · halted at {halted}" if halted else ""
    return {
        "label": f"{tape.symbol} · {tape.status.upper()} · {stamp}{marker}",
        "value": tape.decision_id,
    }


def _claim_table(rows):
    if not rows:
        return html.Div(
            "No scored claims were recorded for this decision.",
            className="text-muted small",
        )
    header = html.Thead(
        html.Tr(
            [
                html.Th("Claim"),
                html.Th("Source"),
                html.Th("Stance"),
                html.Th("Score"),
                html.Th("Fresh"),
                html.Th("Numeric"),
                html.Th("Contra"),
            ]
        )
    )
    body = html.Tbody(
        [
            html.Tr(
                [
                    html.Td(
                        [
                            html.Code(row["claim_id"], className="me-2 small"),
                            row.get("claim", ""),
                        ]
                    ),
                    html.Td(row.get("source", "")),
                    html.Td(
                        dbc.Badge(
                            str(row.get("direction", "")).title(),
                            color={
                                "bullish": "success",
                                "bearish": "danger",
                            }.get(row.get("direction"), "secondary"),
                        )
                    ),
                    html.Td(f"{row.get('confidence', 0.0):.2f}"),
                    html.Td(f"{row.get('freshness', 0.0):.2f}"),
                    html.Td(f"{row.get('numeric_support', 0.0):.2f}"),
                    html.Td(f"{row.get('contradiction', 0.0):.2f}"),
                ]
            )
            for row in rows
        ]
    )
    return dbc.Table(
        [header, body], bordered=False, hover=True, responsive=True, size="sm"
    )


def _gate_rows(ledger):
    if ledger is None:
        return html.Div(
            "No gate ledger was recorded for this decision.",
            className="text-muted small",
        )
    rows = []
    for gate in ledger.gates:
        color = GATE_STATUS_COLORS.get(gate.status.value, "")
        detail = []
        if gate.reasons:
            detail.append(html.Div("; ".join(gate.reasons), className="small"))
        if gate.notional_before is not None and gate.notional_after is not None:
            detail.append(
                html.Div(
                    f"${gate.notional_before:,.0f} → ${gate.notional_after:,.0f}",
                    className="small text-muted",
                )
            )
        if gate.metrics:
            detail.append(
                html.Div(
                    ", ".join(
                        f"{key}: {value}"
                        for key, value in gate.metrics.items()
                        if value is not None and not isinstance(value, (dict, list))
                    ),
                    className="small text-muted",
                )
            )
        rows.append(
            html.Tr(
                [
                    html.Td(gate.label),
                    html.Td(
                        html.Span(
                            gate.status.value.upper(),
                            style={"color": color, "fontWeight": 600},
                        )
                    ),
                    html.Td(detail or html.Span("—", className="text-muted")),
                ]
            )
        )
    return dbc.Table(
        [
            html.Thead(html.Tr([html.Th("Gate"), html.Th("Verdict"), html.Th("Detail")])),
            html.Tbody(rows),
        ],
        bordered=False,
        hover=True,
        responsive=True,
        size="sm",
    )


def _debate_block(title, debate):
    if not debate or not debate.get("transcript"):
        return None
    sides = [
        (label, (debate.get(key) or {}).get("text", ""))
        for label, key in (
            ("Bull", "bull"),
            ("Bear", "bear"),
            ("Risky", "risky"),
            ("Safe", "safe"),
            ("Neutral", "neutral"),
        )
        if debate.get(key)
    ]
    return html.Details(
        [
            html.Summary(
                f"{title} — {debate.get('rounds', 0)} round(s)",
                className="workbench-summary",
            ),
            html.Div(
                [
                    *[
                        html.Div(
                            [html.Strong(f"{label}: "), html.Span(text)],
                            className="mb-2 small",
                        )
                        for label, text in sides
                        if text
                    ],
                    html.Div(
                        [html.Strong("Verdict: "), html.Span(debate.get("verdict", ""))],
                        className="small",
                    ),
                ],
                className="p-2",
            ),
        ],
        className="workbench-details",
    )


def _stage_card(tape, stage):
    body = []
    if stage.key == "compute":
        body.append(_claim_table(tape.claim_rows()))
    elif stage.key == "decide":
        decide = stage.detail or {}
        for title, key in (
            ("Research debate", "research_debate"),
            ("Risk debate", "risk_debate"),
        ):
            block = _debate_block(title, decide.get(key))
            if block is not None:
                body.append(block)
        if decide.get("final_decision"):
            body.append(
                html.Div(
                    [html.Strong("Final decision: "), decide["final_decision"]],
                    className="small mt-2",
                )
            )
    elif stage.key == "prepare":
        body.append(_gate_rows(tape.gate_ledger))
    elif stage.key == "analyze":
        reports = (stage.detail or {}).get("reports", [])
        body.append(
            html.Div(
                [
                    dbc.Badge(
                        f"{item['label']} · {item['chars']:,} chars",
                        color="success" if item["produced"] else "secondary",
                        className="me-1 mb-1",
                    )
                    for item in reports
                ]
            )
        )
    elif stage.key == "order":
        orders = (stage.detail or {}).get("orders", [])
        if orders:
            body.append(
                dbc.Table(
                    [
                        html.Thead(
                            html.Tr(
                                [
                                    html.Th("Broker order"),
                                    html.Th("Side"),
                                    html.Th("Status"),
                                    html.Th("Filled"),
                                    html.Th("Avg price"),
                                ]
                            )
                        ),
                        html.Tbody(
                            [
                                html.Tr(
                                    [
                                        html.Td(order.get("broker_order_id") or "—"),
                                        html.Td(order.get("side", "")),
                                        html.Td(order.get("status", "")),
                                        html.Td(f"{order.get('filled_quantity', 0):g}"),
                                        html.Td(
                                            f"{order.get('filled_avg_price') or 0:,.2f}"
                                        ),
                                    ]
                                )
                                for order in orders
                            ]
                        ),
                    ],
                    bordered=False,
                    size="sm",
                    responsive=True,
                )
            )
    elif stage.key == "react":
        outcomes = (stage.detail or {}).get("outcomes", [])
        body.append(
            html.Div(
                [
                    dbc.Badge(
                        f"{item['horizon']}: {item.get('excess_return_pct', 0):+.2f}%",
                        color=(
                            "success" if item.get("directionally_correct") else "danger"
                        ),
                        className="me-1",
                    )
                    for item in outcomes
                ]
            )
        )
    elif stage.key == "gather":
        provenance = (stage.detail or {}).get("provenance") or {}
        if provenance:
            body.append(
                html.Div(
                    ", ".join(
                        f"{key}: {value}"
                        for key, value in provenance.items()
                        if not isinstance(value, (dict, list))
                    ),
                    className="small text-muted",
                )
            )

    return dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        html.Div(
                            [
                                html.Span(stage.label, className="workbench-stage-name"),
                                dbc.Badge(
                                    stage.state.value.upper(),
                                    color=STATE_BADGES.get(
                                        stage.state.value, "secondary"
                                    ),
                                    className="ms-2",
                                ),
                            ]
                        ),
                        html.Span(stage.headline, className="text-muted small"),
                    ],
                    className="d-flex justify-content-between align-items-center",
                ),
                *([html.Hr(className="my-2")] if body else []),
                *body,
            ]
        ),
        className="workbench-stage-card mb-2",
    )


def render_tape(tape):
    """The full tape: header, then one card per stage."""
    if tape is None:
        return dbc.Alert(NEEDS_POSTGRES, color="warning")

    header = html.Div(
        [
            html.Div(
                [
                    html.H4(tape.symbol, className="mb-0 d-inline-block me-2"),
                    dbc.Badge(tape.status.upper(), color="primary"),
                    *(
                        [dbc.Badge(tape.final_signal, color="info", className="ms-1")]
                        if tape.final_signal
                        else []
                    ),
                ]
            ),
            html.Code(tape.decision_id, className="small text-muted"),
        ],
        className="d-flex justify-content-between align-items-center mb-2",
    )
    alert = None
    if tape.halted_at:
        stage = tape.stage(tape.halted_at)
        alert = dbc.Alert(
            [
                html.Strong(f"Halted at {stage.label}. "),
                stage.headline or tape.error or "",
            ],
            color="danger",
            className="py-2",
        )
    return html.Div(
        [
            header,
            *([alert] if alert is not None else []),
            *[_stage_card(tape, stage) for stage in tape.stages],
        ]
    )


def register_workbench_callbacks(app):
    @app.callback(
        Output("workbench-selection", "options"),
        Output("workbench-selection", "value"),
        Input("workbench-interval", "n_intervals"),
        Input("workbench-refresh", "n_clicks"),
        Input("workbench-symbol", "value"),
        State("workbench-selection", "value"),
    )
    def load_decisions(_interval, _refresh, symbol, selected):
        board = load_board(symbol=symbol)
        if not board:
            return [], None
        options = [decision_option(tape) for tape in board]
        values = {option["value"] for option in options}
        return options, (
            selected if selected in values else options[0]["value"]
        )

    @app.callback(
        Output("workbench-tape", "children"),
        Output("workbench-rail", "children"),
        Output("workbench-gate-waterfall", "figure"),
        Output("workbench-evidence", "figure"),
        Output("workbench-outcomes", "figure"),
        Input("workbench-selection", "value"),
        Input("theme-store", "data"),
    )
    def render_selected(decision_id, theme=DEFAULT_THEME):
        if not decision_id:
            blank = empty_figure("Select a decision", theme=theme)
            return (
                html.Div(
                    "No decision selected.", className="text-muted py-4 text-center"
                ),
                html.Div(),
                blank,
                blank,
                blank,
            )
        try:
            tape = load_tape(decision_id)
        except Exception as exc:
            blank = empty_figure("Unavailable", theme=theme)
            return (
                dbc.Alert(f"Unable to load decision: {exc}", color="danger"),
                html.Div(),
                blank,
                blank,
                blank,
            )
        if tape is None:
            blank = empty_figure("PostgreSQL required", theme=theme)
            return (
                dbc.Alert(NEEDS_POSTGRES, color="warning"),
                html.Div(),
                blank,
                blank,
                blank,
            )
        return (
            render_tape(tape),
            stage_rail(tape.stages, active=tape.current_stage),
            gate_waterfall_figure(
                tape.gate_ledger.waterfall() if tape.gate_ledger else [],
                theme=theme,
            ),
            evidence_figure(tape.evidence_bars(), theme=theme),
            outcome_figure(tape.outcomes, theme=theme),
        )
