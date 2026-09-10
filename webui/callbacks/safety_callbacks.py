"""
Safety-layer callbacks for TradingAgents WebUI
Renders live guardrail status and drives the kill switch.
"""

import dash_bootstrap_components as dbc
from dash import Input, Output, State, ctx, html

from webui.components.safety_panel import SAFETY_LIMIT_FIELDS
from webui.config.constants import COLORS

_GUARD_LABELS = {
    "kill_switch": "Kill Switch",
    "trade_notional": "Trade Size Cap",
    "concentration": "Concentration",
    "daily_loss": "Daily Loss Breaker",
    "drawdown": "Drawdown Breaker",
    "rejection_streak": "Rejection Streak",
    "llm_budget": "LLM Budget",
}


def _guard_detail_text(name, guard):
    detail = guard.get("detail") or {}
    if guard.get("status") == "skipped":
        return str(detail.get("detail", "no account data"))
    if name == "daily_loss" and "change_pct" in detail:
        return f"{detail['change_pct']:+.2f}% today"
    if name == "drawdown" and "drawdown_pct" in detail:
        return f"-{detail['drawdown_pct']:.2f}% from peak"
    if name == "rejection_streak":
        return f"{detail.get('streak', 0)} in a row"
    if name == "llm_budget":
        budget = detail.get("budget") or 0
        if not budget:
            return "unlimited"
        return f"{detail.get('used', 0):,.0f} / {budget:,.0f} tokens"
    if name == "trade_notional":
        cap = detail.get("cap") or 0
        return f"cap ${cap:,.0f}" if cap else "uncapped"
    if name == "concentration":
        limit = detail.get("limit")
        return f"limit ${limit:,.0f}" if limit else str(detail.get("detail", ""))
    return ""


def _status_cards(status):
    cards = []
    for name, label in _GUARD_LABELS.items():
        guard = status["guards"].get(name)
        if guard is None:
            continue
        if guard.get("status") == "skipped":
            color = COLORS.get("pending", "#9ca3af")
            icon = "fas fa-question-circle"
        elif guard["ok"]:
            color = COLORS.get("completed", "#22c55e")
            icon = "fas fa-check-circle"
        else:
            color = COLORS.get("error", "#ef4444")
            icon = "fas fa-exclamation-triangle"
        cards.append(
            dbc.Col(
                dbc.Card(
                    dbc.CardBody(
                        [
                            html.Div(
                                [html.I(className=f"{icon} me-2", style={"color": color}), label],
                                className="small fw-bold",
                            ),
                            html.Div(
                                _guard_detail_text(name, guard),
                                className="text-muted small",
                            ),
                        ],
                        className="p-2",
                    ),
                    style={
                        "backgroundColor": COLORS["card"],
                        "border": f"1px solid {color}",
                    },
                ),
                md=3,
                xs=6,
                className="mb-2",
            )
        )
    rows = [dbc.Row(cards, className="g-2")]
    if status.get("reasons"):
        rows.append(
            dbc.Alert(
                [html.Div(reason) for reason in status["reasons"]],
                color="danger",
                className="mt-2 mb-0 py-2 small",
            )
        )
    if not status.get("enabled", True):
        rows.append(
            dbc.Alert(
                "Safety layer is DISABLED via configuration (safety_enabled=False).",
                color="warning",
                className="mt-2 mb-0 py-2 small",
            )
        )
    return html.Div(rows)


def _account_snapshot():
    try:
        from tradingagents.broker import get_execution_broker_runtime

        snapshot = get_execution_broker_runtime().snapshot_provider.get_portfolio_snapshot()
        return {
            "equity": snapshot.account.equity,
            "last_equity": snapshot.account.last_equity,
        }
    except Exception:
        return None




SAFETY_LIMIT_KEYS = tuple(key for _id, key, _label, _help, _step in SAFETY_LIMIT_FIELDS)


def _number(value, fallback):
    """Blank inputs keep the stored value rather than resetting it to zero."""
    if value is None or value == "":
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{value!r} is not a number")


def read_safety_limits():
    """Current gateway, master switch, and thresholds, in field order."""
    from tradingagents.dataflows.config import get_config
    from tradingagents.safety import DEFAULT_SAFETY_CONFIG

    config = get_config() or {}
    gateway = str(config.get("execution_gateway") or "alpaca")
    enabled = bool(config.get("safety_enabled", True))
    values = [
        config.get(key, DEFAULT_SAFETY_CONFIG.get(key, 0)) for key in SAFETY_LIMIT_KEYS
    ]
    return gateway, enabled, values


def apply_safety_limits(gateway, enabled, values):
    """Persist the edited limits and rebuild the guard that enforces them.

    The guard is a process-wide singleton built from config once, so it has
    to be reset or the new thresholds would not apply until a restart.
    """
    from tradingagents.dataflows.config import get_config, set_config
    from tradingagents.safety import reset_safety_guard

    current = get_config() or {}
    try:
        updates = {
            "execution_gateway": gateway or "alpaca",
            "safety_enabled": bool(enabled),
        }
        for key, value in zip(SAFETY_LIMIT_KEYS, values):
            updates[key] = _number(value, current.get(key, 0))
        if str(updates["execution_gateway"]).lower() not in {"alpaca", "dry-run"}:
            raise ValueError(f"unknown execution gateway {updates['execution_gateway']!r}")
        set_config(updates)
    except Exception as exc:
        return False, f"Limits not saved: {exc}"

    reset_safety_guard()
    if updates["execution_gateway"] == "dry-run":
        return True, "Limits saved. Dry run: plans are journaled, no orders are sent."
    if not updates["safety_enabled"]:
        return True, "Limits saved. Safety layer is DISABLED."
    return True, "Limits saved and guardrails reloaded."



def register_safety_callbacks(app):
    """Register safety-layer callbacks with the Dash app"""

    @app.callback(
        [
            Output("safety-status-container", "children"),
            Output("safety-action-status", "children"),
        ],
        [
            Input("safety-refresh-interval", "n_intervals"),
            Input("safety-kill-switch-btn", "n_clicks"),
            Input("safety-release-btn", "n_clicks"),
        ],
        prevent_initial_call=False,
    )
    def refresh_safety_panel(_n, _engage_clicks, _release_clicks):
        from tradingagents.safety import get_safety_guard

        guard = get_safety_guard()
        action_message = None

        trigger = ctx.triggered_id if ctx.triggered_id else None
        if trigger == "safety-kill-switch-btn":
            guard.engage_kill_switch("engaged from WebUI")
            action_message = dbc.Alert(
                "Kill switch ENGAGED — all order flow is halted.",
                color="danger",
                className="mb-0 py-2 small",
            )
        elif trigger == "safety-release-btn":
            guard.release_kill_switch()
            action_message = dbc.Alert(
                "Kill switch released — order flow may resume.",
                color="success",
                className="mb-0 py-2 small",
            )

        status = guard.status(account=_account_snapshot())
        return _status_cards(status), action_message

    @app.callback(
        Output("safety-limits-collapse", "is_open"),
        Input("safety-limits-toggle", "n_clicks"),
        State("safety-limits-collapse", "is_open"),
        prevent_initial_call=True,
    )
    def toggle_safety_limits(_clicks, is_open):
        return not is_open

    @app.callback(
        Output("safety-execution-gateway", "value"),
        Output("safety-enabled-switch", "value"),
        *[
            Output(input_id, "value")
            for input_id, _key, _label, _help, _step in SAFETY_LIMIT_FIELDS
        ],
        Input("safety-limits-collapse", "is_open"),
        Input("safety-limits-save", "n_clicks"),
    )
    def load_safety_limits(_is_open, _save_clicks):
        gateway, enabled, values = read_safety_limits()
        return (gateway, enabled, *values)

    @app.callback(
        Output("safety-limits-status", "children"),
        Input("safety-limits-save", "n_clicks"),
        State("safety-execution-gateway", "value"),
        State("safety-enabled-switch", "value"),
        *[
            State(input_id, "value")
            for input_id, _key, _label, _help, _step in SAFETY_LIMIT_FIELDS
        ],
        prevent_initial_call=True,
    )
    def save_safety_limits(_clicks, gateway, enabled, *values):
        saved, message = apply_safety_limits(gateway, enabled, values)
        return dbc.Alert(
            message,
            color="success" if saved else "danger",
            className="mb-0 py-2 small",
        )
