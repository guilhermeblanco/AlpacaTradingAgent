"""Callbacks for the Set up stage and the guided wizard.

Reading readiness is cheap — it asks each layer whether a credential is
configured, never what it is — so the Set up panel recomputes on an
interval and nothing has to tell it that a key was saved.

The wizard is the opposite: its list of steps is fixed when it opens and
kept in a store. That is not laziness, it is the fix for a real bug. Saving
a key satisfies a requirement, which would drop it from a recomputed list —
and since the step index has already advanced by one, the step *after* the
one you just completed would be skipped entirely. A wizard whose length
changes underneath you also makes its own progress bar a lie. Reopening it
recomputes; walking through it does not.
"""

from dash import ALL, Input, Output, State, ctx, html, no_update
import dash_bootstrap_components as dbc

from webui.components.setup_panel import readiness_summary
from webui.components.setup_wizard import (
    POSTURE_STEP,
    WELCOME_STEP,
    posture_step,
    requirement_step,
    welcome_step,
)
from webui.config.navigation import SETUP_STAGE


def current_config():
    from tradingagents.dataflows.config import get_config

    try:
        return get_config()
    except Exception:
        return {}


def current_readiness():
    """Readiness for the running configuration, or None if unreadable."""
    from tradingagents.setup import evaluate_readiness

    try:
        return evaluate_readiness(current_config())
    except Exception:
        try:
            return evaluate_readiness({})
        except Exception:
            return None


def build_plan(readiness):
    """The step ids for one run of the wizard, fixed at open time."""
    from tradingagents.setup import setup_steps

    outstanding = setup_steps(readiness) if readiness is not None else []
    return [WELCOME_STEP, *(item.id for item in outstanding), POSTURE_STEP]


def _title(step_id, requirement):
    if step_id == WELCOME_STEP:
        return "Set up"
    if step_id == POSTURE_STEP:
        return "Before you go"
    return requirement.title if requirement else step_id


def register_setup_callbacks(app):
    # ── The Set up panel ─────────────────────────────────────────────────
    @app.callback(
        Output("setup-readiness", "children"),
        Input("setup-readiness-interval", "n_intervals"),
        Input("api-config-modal", "is_open"),
        Input("wizard-save-status", "children"),
    )
    def show_readiness(_intervals, _modal_open, _saved):
        # Recomputed when the integrations modal closes and when the wizard
        # saves — the two moments a credential is likely to have changed.
        return readiness_summary(current_readiness())

    @app.callback(
        Output("api-config-modal", "is_open", allow_duplicate=True),
        Input("open-api-config-from-setup-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def open_integrations(n_clicks):
        return True if n_clicks else no_update

    # ── Opening, and opening itself ──────────────────────────────────────
    @app.callback(
        Output("setup-wizard-modal", "is_open"),
        Output("wizard-dismissed", "data"),
        Output("wizard-plan", "data"),
        Output("wizard-step", "data"),
        Output("stage-tabs", "active_tab", allow_duplicate=True),
        Input("open-setup-wizard-btn", "n_clicks"),
        Input("setup-wizard-modal", "is_open"),
        Input("refresh-interval", "n_intervals"),
        State("wizard-dismissed", "data"),
        prevent_initial_call="initial_duplicate",
    )
    def toggle_wizard(open_clicks, is_open, _intervals, dismissed):
        triggered = getattr(ctx, "triggered_id", None)

        def opened():
            return True, False, build_plan(current_readiness()), 0, SETUP_STAGE

        if triggered == "open-setup-wizard-btn" and open_clicks:
            return opened()

        # The modal reporting itself closed is the operator dismissing it.
        # Remember that, or it reopens on the next tick and cannot be
        # escaped.
        if triggered == "setup-wizard-modal" and not is_open:
            return False, True, no_update, 0, no_update

        if dismissed or is_open:
            return no_update, no_update, no_update, no_update, no_update

        readiness = current_readiness()
        if readiness is not None and not readiness.ready:
            # An unconfigured deployment otherwise opens on a workbench of
            # empty panels, each correctly reporting that nothing has been
            # recorded, none of which says why.
            return opened()

        return no_update, no_update, no_update, no_update, no_update

    # ── Moving through it ────────────────────────────────────────────────
    @app.callback(
        Output("wizard-step", "data", allow_duplicate=True),
        Output("setup-wizard-modal", "is_open", allow_duplicate=True),
        Input("wizard-next", "n_clicks"),
        Input("wizard-back", "n_clicks"),
        State("wizard-step", "data"),
        State("wizard-plan", "data"),
        prevent_initial_call=True,
    )
    def move(_next_clicks, _back_clicks, step, plan):
        triggered = getattr(ctx, "triggered_id", None)
        step = int(step or 0)
        plan = list(plan or [WELCOME_STEP, POSTURE_STEP])

        if triggered == "wizard-back":
            return max(step - 1, 0), no_update
        if triggered == "wizard-next":
            if step >= len(plan) - 1:
                # "Done" on the last step. Closing here rather than letting
                # the index run past the end, which would leave the modal
                # open on a clamped final screen with a dead button.
                return 0, False
            return step + 1, no_update
        return no_update, no_update

    @app.callback(
        Output("wizard-title", "children"),
        Output("wizard-body", "children"),
        Output("wizard-progress", "children"),
        Output("wizard-step-count", "children"),
        Output("wizard-back", "disabled"),
        Output("wizard-next", "children"),
        Input("wizard-step", "data"),
        Input("wizard-plan", "data"),
        Input("setup-wizard-modal", "is_open"),
        Input("wizard-save-status", "children"),
    )
    def render(step, plan, is_open, _saved):
        if not is_open:
            return (no_update,) * 6

        plan = list(plan or [WELCOME_STEP, POSTURE_STEP])
        index = min(max(int(step or 0), 0), len(plan) - 1)
        step_id = plan[index]

        # The sequence is fixed, but the *contents* are read fresh, so a
        # credential saved a moment ago shows as "already set" rather than
        # as an empty box.
        readiness = current_readiness()
        requirement = readiness.get(step_id) if readiness else None

        if step_id == WELCOME_STEP:
            outstanding = [
                readiness.get(item)
                for item in plan
                if item not in (WELCOME_STEP, POSTURE_STEP)
            ]
            body = welcome_step(readiness, [item for item in outstanding if item])
        elif step_id == POSTURE_STEP:
            body = posture_step(current_config())
        elif requirement is not None:
            body = requirement_step(requirement)
        else:
            body = html.Div(
                f"{step_id} is no longer part of this configuration.",
                className="text-muted",
            )

        last = index == len(plan) - 1
        return (
            _title(step_id, requirement),
            body,
            dbc.Progress(
                value=100 * (index + 1) / max(len(plan), 1),
                color="primary",
                style={"height": "4px"},
            ),
            f"Step {index + 1} of {len(plan)}",
            index == 0,
            "Done" if last else "Next",
        )

    # ── Saving ───────────────────────────────────────────────────────────
    @app.callback(
        Output("wizard-save-status", "children"),
        Input("wizard-next", "n_clicks"),
        State({"type": "wizard-credential", "key": ALL}, "value"),
        State({"type": "wizard-credential", "key": ALL}, "id"),
        prevent_initial_call=True,
    )
    def save(_clicks, values, ids):
        """Store whatever the step being left had collected.

        Blank means "leave it alone", not "clear it". The field is labelled
        "already set" when there is a value behind it, and a wizard that
        wiped a working credential because somebody tabbed past the box
        would be indefensible.
        """
        from tradingagents.integrations import get_integration_vault

        entered = {
            identifier["key"]: value.strip()
            for identifier, value in zip(ids or [], values or [])
            if isinstance(value, str) and value.strip()
        }
        if not entered:
            return no_update

        vault = get_integration_vault()
        if vault is None:
            return dbc.Alert(
                [
                    html.Strong("Nothing was saved. "),
                    "The encrypted vault is not configured, so there is "
                    "nowhere to put these. Set INTEGRATION_VAULT_KEY and "
                    "DATABASE_URL in the deployment's environment, or set "
                    "the credentials there directly.",
                ],
                color="warning",
                className="py-2 mb-0",
            )

        try:
            stored = vault.set_many(entered, actor="setup-wizard")
        except Exception as exc:
            return dbc.Alert(
                f"Unable to save: {exc}", color="danger", className="py-2 mb-0"
            )

        return dbc.Alert(
            f"Saved {stored} credential{'s' if stored != 1 else ''} to the "
            "encrypted vault.",
            color="success",
            className="py-2 mb-0",
        )
