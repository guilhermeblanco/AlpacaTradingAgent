"""Callbacks for preview, replay, and operator overrides.

The three actions that turn the workbench from something you read into
something you operate. All of them were already supported by the backend
and none of them had a surface.
"""

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, ctx, html

from tradingagents.workbench.preview import (
    PreviewUnavailable,
    compare_ledgers,
    preview_ledger,
    replay_tape,
    resume_scope,
)
from webui.callbacks.board_callbacks import load_health
from webui.callbacks.workbench_callbacks import load_tape
from webui.components.workbench import gate_waterfall_figure
from webui.config.tokens import DEFAULT_THEME, status_color
from webui.utils.persistence import get_persistence_runtime


def paused_scopes():
    """Quarantined scopes an operator could lift, or an empty list."""
    try:
        health = load_health()
    except Exception:
        return []
    if health is None:
        return []
    return [control for control in health.controls if control.paused]


def render_overrides(controls):
    """One resume control per paused scope, with the reason it was paused."""
    if not controls:
        return html.Div()
    return dbc.Alert(
        [
            html.Div(
                [
                    html.Strong("Execution is quarantined. "),
                    "Lifting a quarantine is recorded against your user.",
                ],
                className="mb-2",
            ),
            *[
                html.Div(
                    [
                        html.Code(control.service, className="me-2"),
                        html.Span(
                            control.reason or "paused", className="me-2 small"
                        ),
                        dbc.Button(
                            "Resume",
                            id={"type": "resume-scope", "scope": control.service},
                            color="danger",
                            outline=True,
                            size="sm",
                            n_clicks=0,
                        ),
                    ],
                    className="d-flex align-items-center mb-1",
                )
                for control in controls
            ],
        ],
        color="danger",
        className="py-2 mb-0",
    )


def render_preview(result, original_ledger):
    """The hypothetical ledger, and what it changed relative to the original."""
    ledger = preview_ledger(result)
    if ledger is None:
        return dbc.Alert(
            result.get("error") or "The preview produced no gate ledger.",
            color="warning",
            className="py-2",
        )

    rows = compare_ledgers(original_ledger, ledger)
    changed = [row for row in rows if row["changed"]]
    summary = dbc.Alert(
        ledger.summary(),
        color="success" if ledger.allowed else "danger",
        className="py-2 mb-2",
    )
    table = dbc.Table(
        [
            html.Thead(
                html.Tr([html.Th("Gate"), html.Th("Was"), html.Th("Now"), html.Th("Why")])
            ),
            html.Tbody(
                [
                    html.Tr(
                        [
                            html.Td(row["label"]),
                            html.Td(row["was"] or "—"),
                            html.Td(
                                html.Span(
                                    row["now"],
                                    style={
                                        "color": status_color(row["now"]),
                                        "fontWeight": 600,
                                    },
                                )
                            ),
                            html.Td("; ".join(row["reasons"]) or "—"),
                        ],
                        className="table-warning" if row["changed"] else None,
                    )
                    for row in rows
                ]
            ),
        ],
        bordered=False,
        size="sm",
        responsive=True,
    )
    note = (
        html.Div(
            f"{len(changed)} gate(s) would decide differently.",
            className="small text-muted mb-2",
        )
        if changed
        else html.Div(
            "Every gate would decide the same way.",
            className="small text-muted mb-2",
        )
    )
    return html.Div([summary, note, table])


def register_override_callbacks(app):
    @app.callback(
        Output("vitals-overrides", "children"),
        Input("vitals-interval", "n_intervals"),
        Input("vitals-override-result", "children"),
    )
    def show_overrides(_intervals, _result):
        return render_overrides(paused_scopes())

    @app.callback(
        Output("vitals-override-result", "children"),
        Input({"type": "resume-scope", "scope": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def resume(clicks):
        """Lift one quarantine, and say so."""
        if not clicks or not any(clicks) or not ctx.triggered_id:
            return ""
        scope = (ctx.triggered_id or {}).get("scope")
        if not scope:
            return ""
        runtime = get_persistence_runtime()
        if runtime.unit_of_work_factory is None:
            return dbc.Alert(
                "Overrides require PostgreSQL.", color="warning", className="py-2 mt-2"
            )
        try:
            outcome = resume_scope(
                runtime.unit_of_work_factory,
                scope,
                actor="webui",
                reason="Resumed from the workbench",
            )
        except Exception as exc:
            return dbc.Alert(
                f"Could not resume {scope}: {exc}", color="danger", className="py-2 mt-2"
            )
        return dbc.Alert(
            f"Resumed {outcome['scope']}. The override is recorded.",
            color="success",
            className="py-2 mt-2",
            duration=8000,
        )

    @app.callback(
        Output("workbench-preview-result", "children"),
        Output("workbench-gate-waterfall", "figure", allow_duplicate=True),
        Input("workbench-preview", "n_clicks"),
        State("workbench-selection", "value"),
        State("workbench-preview-notional", "value"),
        State("theme-store", "data"),
        prevent_initial_call=True,
    )
    def preview(n_clicks, decision_id, notional, theme=DEFAULT_THEME):
        from dash import no_update

        if not n_clicks or not decision_id:
            return "", no_update
        try:
            tape = load_tape(decision_id)
        except Exception as exc:
            return (
                dbc.Alert(f"Unable to load decision: {exc}", color="danger",
                          className="py-2"),
                no_update,
            )
        if tape is None:
            return (
                dbc.Alert("Previews require PostgreSQL.", color="warning",
                          className="py-2"),
                no_update,
            )
        try:
            result = replay_tape(
                tape,
                requested_notional=float(notional) if notional else None,
            )
        except PreviewUnavailable as exc:
            return dbc.Alert(str(exc), color="warning", className="py-2"), no_update
        except Exception as exc:
            return (
                dbc.Alert(f"Preview failed: {exc}", color="danger", className="py-2"),
                no_update,
            )

        ledger = preview_ledger(result)
        figure = (
            gate_waterfall_figure(ledger.waterfall(), theme=theme)
            if ledger is not None
            else no_update
        )
        return render_preview(result, tape.gate_ledger), figure
