"""
Safety-layer callbacks for TradingAgents WebUI
Renders live guardrail status and drives the kill switch.
"""

import dash_bootstrap_components as dbc
from dash import Input, Output, ctx, html

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
                        "backgroundColor": COLORS.get("card", "#1f2937"),
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
