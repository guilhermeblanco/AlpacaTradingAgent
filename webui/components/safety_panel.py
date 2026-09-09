"""
webui/components/safety_panel.py - Production safety layer status panel

Shows a green/red badge for every deterministic guard (kill switch,
pre-trade checks, circuit breakers, LLM budget) plus a kill-switch toggle
that halts all order flow immediately, regardless of agent decisions.

The limits behind those guards are editable here too, alongside the
execution gateway: dry-run produces plans and journals without sending a
single broker order, which is the safest way to try a configuration.
"""

import dash_bootstrap_components as dbc
from dash import dcc, html




# (input id, label, help text, step)
SAFETY_LIMIT_FIELDS = (
    (
        "safety-limit-trade-notional",
        "max_trade_notional_usd",
        "Max order notional (USD)",
        "Per-order cap. 0 = uncapped.",
        100,
    ),
    (
        "safety-limit-concentration",
        "max_symbol_concentration_pct",
        "Max symbol concentration (%)",
        "Per-symbol exposure as % of equity. 0 = uncapped.",
        1,
    ),
    (
        "safety-limit-daily-loss",
        "daily_loss_halt_pct",
        "Daily loss halt (%)",
        "Halt when equity falls this far below yesterday.",
        1,
    ),
    (
        "safety-limit-drawdown",
        "max_drawdown_halt_pct",
        "Drawdown halt (%)",
        "Halt when equity falls this far below the high-water mark.",
        1,
    ),
    (
        "safety-limit-rejections",
        "max_consecutive_rejections",
        "Consecutive rejection halt",
        "Halt after this many broker rejections in a row.",
        1,
    ),
    (
        "safety-limit-token-budget",
        "daily_llm_token_budget",
        "Daily LLM token budget",
        "Refuse new analyses past this many tokens per day. 0 = unlimited.",
        1000,
    ),
)


def _limit_input(input_id, label, help_text, step):
    return dbc.Col(
        [
            dbc.Label(label, html_for=input_id, className="small mb-1"),
            dbc.Input(id=input_id, type="number", min=0, step=step, size="sm"),
            html.Div(help_text, className="text-muted", style={"fontSize": "0.72rem"}),
        ],
        md=4,
        xs=6,
        className="mb-3",
    )


def create_safety_limits_section():
    """Editable execution gateway and deterministic guardrail thresholds."""
    return html.Div(
        [
            html.Hr(),
            dbc.Button(
                [html.I(className="fas fa-sliders me-2"), "Execution limits"],
                id="safety-limits-toggle",
                color="link",
                size="sm",
                className="p-0 mb-2",
            ),
            dbc.Collapse(
                [
                    dbc.Row(
                        [
                            dbc.Col(
                                [
                                    dbc.Label(
                                        "Execution gateway",
                                        html_for="safety-execution-gateway",
                                        className="small mb-1",
                                    ),
                                    dbc.Select(
                                        id="safety-execution-gateway",
                                        options=[
                                            {"label": "Broker (live order flow)", "value": "alpaca"},
                                            {"label": "Dry run (plan only, no orders)", "value": "dry-run"},
                                        ],
                                        size="sm",
                                    ),
                                    html.Div(
                                        "Dry run journals every plan without sending it.",
                                        className="text-muted",
                                        style={"fontSize": "0.72rem"},
                                    ),
                                ],
                                md=4,
                                xs=12,
                                className="mb-3",
                            ),
                            dbc.Col(
                                [
                                    dbc.Label("Safety layer", className="small mb-1"),
                                    dbc.Switch(
                                        id="safety-enabled-switch",
                                        label="Enforce pre-trade checks and breakers",
                                        value=True,
                                    ),
                                ],
                                md=8,
                                xs=12,
                                className="mb-3",
                            ),
                        ]
                    ),
                    dbc.Row(
                        [
                            _limit_input(input_id, label, help_text, step)
                            for input_id, _key, label, help_text, step in SAFETY_LIMIT_FIELDS
                        ]
                    ),
                    dbc.Button(
                        "Save limits",
                        id="safety-limits-save",
                        color="primary",
                        size="sm",
                    ),
                    html.Div(id="safety-limits-status", className="mt-2"),
                ],
                id="safety-limits-collapse",
                is_open=False,
            ),
        ]
    )



def create_safety_panel():
    """Create the safety guardrails card for the web UI."""
    return dbc.Card(
        dbc.CardBody(
            [
                dbc.Row(
                    [
                        dbc.Col(
                            [
                                html.H4("Safety Guardrails", className="mb-1"),
                                html.Div(
                                    "Deterministic pre-trade checks, circuit breakers, and a "
                                    "kill switch — enforced before any order reaches the broker, "
                                    "independent of agent decisions.",
                                    className="text-muted small",
                                ),
                            ],
                            md=8,
                        ),
                        dbc.Col(
                            [
                                dbc.ButtonGroup(
                                    [
                                        dbc.Button(
                                            [
                                                html.I(className="fas fa-hand-paper me-2"),
                                                "Engage Kill Switch",
                                            ],
                                            id="safety-kill-switch-btn",
                                            color="danger",
                                            size="sm",
                                        ),
                                        dbc.Button(
                                            "Release",
                                            id="safety-release-btn",
                                            color="secondary",
                                            outline=True,
                                            size="sm",
                                        ),
                                    ],
                                    className="float-end",
                                ),
                            ],
                            md=4,
                        ),
                    ],
                    className="mb-3 align-items-start",
                ),
                html.Div(id="safety-action-status", className="mb-2"),
                html.Div(id="safety-status-container"),
                create_safety_limits_section(),
                dcc.Interval(
                    id="safety-refresh-interval",
                    interval=30_000,  # 30s
                    n_intervals=0,
                ),
            ]
        ),
        className="mb-4",
    )
