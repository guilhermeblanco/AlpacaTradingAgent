"""Callbacks for the pipeline board and the vitals strip.

The board reads persisted decisions from PostgreSQL and overlays the run
happening in this process, so it is never blank during exactly the minutes
an operator is most likely to be watching it.
"""

import time

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, ctx, html, no_update

from tradingagents.workbench.board import (
    card_from_tape,
    group_by_stage,
    halt_counts,
    live_cards,
    merge_cards,
    stage_counts,
    throughput_series,
)
from tradingagents.workbench.tape import STAGES
from webui.components.pipeline_board import (
    board_column,
    halt_breakdown_figure,
    stage_distribution_figure,
    throughput_figure,
)
from webui.config.tokens import DEFAULT_THEME
from webui.components.vitals import vital
from webui.components.workbench import empty_figure
from webui.callbacks.workbench_callbacks import decision_option, load_tape
from webui.utils.persistence import get_persistence_runtime
from webui.utils.state import app_state


def load_recent(limit=60):
    """Recent tapes, or None when there is no database."""
    runtime = get_persistence_runtime()
    if runtime.unit_of_work_factory is None:
        return None
    with runtime.unit_of_work_factory() as uow:
        return uow.workbench.board(limit=limit)


def load_health(stale_after_seconds=None):
    """Operational health, each service judged against its own cadence.

    The default used to be 120 seconds for everything, which the
    evaluation worker — beating once per 300-second cycle — could never
    satisfy. It showed as permanently dead while its own container
    healthcheck, at 900, called it fine.
    """
    runtime = get_persistence_runtime()
    if runtime.unit_of_work_factory is None:
        return None
    with runtime.unit_of_work_factory() as uow:
        return uow.operations.health(stale_after_seconds=stale_after_seconds)


def mode_reading():
    """What the machine is set to do, from in-process scheduling state."""
    if app_state.screener_enabled:
        return "Screener", "busy", "Scanning the market for candidates"
    if app_state.loop_enabled:
        return (
            "Loop",
            "busy",
            f"Every {app_state.loop_interval_minutes} minutes",
        )
    if app_state.market_hour_enabled:
        hours = ", ".join(f"{hour}:00" for hour in (app_state.market_hours or []))
        return "Market hours", "busy", f"At {hours} EST/EDT" if hours else ""
    if app_state.analysis_running:
        return "Single run", "busy", "One batch in progress"
    return "Idle", "idle", "No schedule armed"


def _health_readings(health):
    """Worker, quarantine, and queue readings from the operational health."""
    paused = [control for control in health.controls if control.paused]
    stale = [beat for beat in health.heartbeats if beat.stale]
    lag = health.reconciliation_oldest_lag_seconds or 0.0
    return [
        vital(
            "Quarantine",
            f"{len(paused)} paused" if paused else "clear",
            "bad" if paused else "ok",
            "; ".join(
                f"{control.service}: {control.reason or 'paused'}"
                for control in paused
            ),
        ),
        # "1/8 live" read as eight expected workers, one of them alive. It
        # was eight rows in a table that never forgot a restart. Say what
        # is actually known: how many are reporting, and name the ones
        # that have gone quiet.
        vital(
            "Workers",
            (
                f"{len(health.heartbeats) - len(stale)} reporting"
                if health.heartbeats
                else "none"
            ),
            "bad" if stale else "ok" if health.heartbeats else "idle",
            "; ".join(f"{beat.service} silent" for beat in stale)
            or "All services reporting on schedule",
        ),
        vital(
            "Reconciliation",
            f"{health.reconciliation_pending} pending",
            "bad" if lag > 300 else "ok",
            f"Oldest {lag:.0f}s behind" if lag else "Nothing waiting",
        ),
        vital(
            "In flight",
            str(health.analyses_in_flight),
            "busy" if health.analyses_in_flight else "idle",
            f"{health.active_reservation_allocations} reserved allocations",
        ),
    ]


def build_vitals():
    """The readings, in the order an operator asks for them."""
    mode, mode_tone, mode_hint = mode_reading()
    analyzing = getattr(app_state, "analyzing_symbol", None)
    readings = [
        vital("Mode", mode, mode_tone, mode_hint),
        vital(
            "Analyzing",
            analyzing or "—",
            "busy" if analyzing else "idle",
            f"{app_state.tool_calls_count} tool calls, "
            f"{app_state.llm_calls_count} LLM calls this session",
        ),
    ]

    try:
        health = load_health()
    except Exception as exc:
        readings.append(vital("Workers", "UNAVAILABLE", "bad", str(exc)))
    else:
        if health is None:
            readings.append(
                vital("Workers", "NO DATABASE", "idle", "Configure DATABASE_URL")
            )
        else:
            readings.extend(_health_readings(health))

    readings.append(_budget_reading())
    return readings


def _budget_reading():
    """How much of the daily LLM token budget is left."""
    try:
        from tradingagents.dataflows.config import get_config
        from tradingagents.safety import get_safety_guard

        budget = int((get_config() or {}).get("daily_llm_token_budget", 0) or 0)
        if budget <= 0:
            return vital("Token budget", "unlimited", "idle", "No daily cap set")
        used = int(getattr(get_safety_guard(), "llm_tokens_today", 0) or 0)
        remaining = max(0, budget - used)
        share = remaining / budget
        return vital(
            "Token budget",
            f"{remaining:,} left",
            "bad" if share < 0.1 else "busy" if share < 0.35 else "ok",
            f"{used:,} of {budget:,} used today",
        )
    except Exception:
        return vital("Token budget", "—", "idle", "")


def build_board(selected=None, *, theme=DEFAULT_THEME):
    """Columns, and the three charts that break the board down.

    `selected` is whichever decision the tape is showing, so the board and
    the tape visibly refer to the same thing. `theme` has to be passed
    rather than read: a figure is rendered here, in Python, and shipped
    as JSON, so it is the one surface that cannot pick up a CSS variable.
    """
    try:
        tapes = load_recent()
    except Exception as exc:
        return (
            dbc.Alert(f"Unable to load the board: {exc}", color="danger"),
            empty_figure("Unavailable", theme=theme),
            empty_figure("Unavailable", theme=theme),
            empty_figure("Unavailable", theme=theme),
        )

    live = live_cards(
        getattr(app_state, "symbol_states", {}) or {},
        analyzing_symbol=getattr(app_state, "analyzing_symbol", None),
    )
    if tapes is None:
        cards = live
        tapes = []
        note = dbc.Alert(
            "Showing the running analysis only. Configure DATABASE_URL to see "
            "past and autonomous decisions.",
            color="warning",
            className="py-2 mb-2",
        )
    else:
        cards = merge_cards(live, [card_from_tape(tape) for tape in tapes])
        note = None

    grouped = group_by_stage(cards)
    columns = html.Div(
        [board_column(label, grouped.get(key, []), selected) for key, label in STAGES],
        className="board-columns",
    )
    return (
        html.Div([note, columns] if note is not None else columns),
        stage_distribution_figure(stage_counts(cards), theme=theme),
        halt_breakdown_figure(halt_counts(tapes), theme=theme),
        throughput_figure(throughput_series(tapes), theme=theme),
    )


LIVE_CARD_NOTE = (
    "That analysis is still running. It gets a decision — and a tape — once "
    "the risk manager produces a typed intent."
)

#: The board sits above the tape, so opening a card has to bring the tape
#: into view or the click looks like it did nothing.
SCROLL_TO_TAPE = """
function(signal) {
    if (!signal) { return window.dash_clientside.no_update; }
    const target = document.getElementById('workbench-tape');
    if (target && target.scrollIntoView) {
        target.scrollIntoView({behavior: 'smooth', block: 'start'});
    }
    return window.dash_clientside.no_update;
}
"""


def open_on_tape(decision_id, options):
    """Select a decision on the tape, adding its option if it is filtered out.

    The board shows more decisions than the tape's dropdown, and the
    dropdown can be narrowed by symbol, so a card can point at something the
    dropdown does not currently list.
    """
    options = list(options or [])
    if any(option.get("value") == decision_id for option in options):
        return options, decision_id
    try:
        tape = load_tape(decision_id)
    except Exception:
        tape = None
    if tape is None:
        return options, decision_id
    return [decision_option(tape), *options], decision_id


def register_board_callbacks(app):
    @app.callback(
        Output("vitals-strip", "children"),
        Input("vitals-interval", "n_intervals"),
    )
    def update_vitals(_intervals):
        return build_vitals()

    @app.callback(
        Output("pipeline-board", "children"),
        Output("board-stage-distribution", "figure"),
        Output("board-halts", "figure"),
        Output("board-throughput", "figure"),
        Input("board-interval", "n_intervals"),
        Input("board-refresh", "n_clicks"),
        Input("workbench-selection", "value"),
        Input("theme-store", "data"),
    )
    def update_board(_intervals, _clicks, selected, theme=DEFAULT_THEME):
        return build_board(selected, theme=theme)

    @app.callback(
        Output("workbench-selection", "options", allow_duplicate=True),
        Output("workbench-selection", "value", allow_duplicate=True),
        Output("board-note", "children"),
        Output("board-scroll", "data"),
        Input({"type": "board-card", "decision": ALL}, "n_clicks"),
        State("workbench-selection", "options"),
        prevent_initial_call=True,
    )
    def open_card(clicks, options):
        """Open the clicked card on the tape below.

        The board re-renders every few seconds, which recreates the cards
        with `n_clicks` back at zero and fires this callback; an all-zero
        list is that re-render, not a click.
        """
        if not clicks or not any(clicks):
            return no_update, no_update, no_update, no_update
        decision_id = (ctx.triggered_id or {}).get("decision")
        if not decision_id:
            return no_update, no_update, no_update, no_update
        if str(decision_id).startswith("live:"):
            return (
                no_update,
                no_update,
                dbc.Alert(LIVE_CARD_NOTE, color="info", className="py-2 mb-0"),
                no_update,
            )
        options, value = open_on_tape(decision_id, options)
        # A fresh value each time, so clicking the same card twice still
        # scrolls rather than being deduplicated away.
        return options, value, "", {"decision_id": value, "at": time.time()}

    app.clientside_callback(
        SCROLL_TO_TAPE,
        Output("board-scroll", "id"),
        Input("board-scroll", "data"),
    )
