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

from webui.components.platform_settings import (
    confirmation_detail,
    settings_body,
)
from webui.components.provider_editor import provider_form
from webui.components.setup_panel import readiness_summary
from webui.components.setup_wizard import (
    POSTURE_STEP,
    WELCOME_STEP,
    posture_step,
    requirement_step,
    welcome_step,
)
from webui.config.navigation import SETUP_STAGE
from tradingagents.setup.providers import role as get_role, selected


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
            # A step whose id is also a role is a choice, not a statement:
            # pick the provider, and its own fields follow.
            role = get_role(step_id)
            body = requirement_step(
                requirement,
                role=role,
                chosen=selected(step_id, current_config()) if role else "",
            )
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

    @app.callback(
        Output("wizard-save-status", "children", allow_duplicate=True),
        Input({"type": "wizard-provider", "role": ALL}, "value"),
        State({"type": "wizard-provider", "role": ALL}, "id"),
        prevent_initial_call=True,
    )
    def choose_provider(values, ids):
        """Store the provider the moment it is picked.

        Stored rather than held in the browser because the fields shown
        underneath come from the readiness model, which reads the
        configuration — so the choice has to be somewhere the server can
        see before it can render the right form.
        """
        from tradingagents.setup.providers import role as find_role
        from tradingagents.setup.settings import get_runtime_settings

        store = get_runtime_settings()
        if store is None:
            return dbc.Alert(
                "Cannot record the choice: no database is configured, so "
                "provider selection has to stay in the environment.",
                color="warning", className="py-2 mb-0",
            )

        changed = []
        config = current_config()
        for identifier, value in zip(ids or [], values or []):
            role = find_role(identifier["role"])
            if role is None or not value or not role.setting:
                continue
            if str(config.get(role.setting) or "").lower() == str(value).lower():
                continue
            try:
                store.set(role.setting, value, actor="setup-wizard")
                changed.append(f"{role.label} → {role.provider(value).label}")
            except Exception as exc:
                return dbc.Alert(
                    f"Unable to record the choice: {exc}",
                    color="danger", className="py-2 mb-0",
                )
        if not changed:
            return no_update
        return dbc.Alert(
            "; ".join(changed), color="info", className="py-2 mb-0"
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


def register_platform_callbacks(app):
    """The settings an operator can change while it runs.

    Split from the wizard because it answers a different question. The
    wizard is "what does this need before it works"; this is "what is it
    allowed to do right now", and the second one is asked repeatedly by
    somebody who already knows the answer to the first.
    """

    @app.callback(
        Output("platform-settings-body", "children"),
        Output("platform-settings-history", "children"),
        Input("platform-settings-interval", "n_intervals"),
        Input("platform-settings-status", "children"),
    )
    def render(_intervals, _status):
        from tradingagents.dataflows.config import get_api_key_source
        from tradingagents.setup.settings import SETTINGS, get_runtime_settings

        config = current_config()
        sources = {}
        for item in SETTINGS:
            try:
                sources[item.key] = get_api_key_source(item.key, item.env_var)
            except Exception:
                sources[item.key] = ""

        store = get_runtime_settings()
        if store is None:
            history = html.Div(
                "No database, so settings cannot be changed here — the "
                "environment answers.",
                className="small text-muted",
            )
        else:
            try:
                rows = store.history(limit=20)
            except Exception as exc:
                rows = []
                history = html.Div(f"Unable to read history: {exc}",
                                   className="small text-muted")
            if rows:
                history = html.Ul(
                    [
                        html.Li(
                            f"{row['at']:%Y-%m-%d %H:%M} · {row['name']}: "
                            f"{row['previous'] or '—'} → {row['new'] or '—'} "
                            f"· {row['actor']}"
                            + (f" · {row['reason']}" if row["reason"] else ""),
                            className="small",
                        )
                        for row in rows
                    ],
                    className="mb-0",
                )
            elif store is not None:
                history = html.Div("Nothing changed yet.",
                                   className="small text-muted")

        return settings_body(config, sources), history

    @app.callback(
        Output("platform-confirm-modal", "is_open"),
        Output("platform-confirm-detail", "children"),
        Output("platform-pending-change", "data"),
        Output("platform-settings-status", "children"),
        Input({"type": "platform-setting", "key": ALL}, "value"),
        State({"type": "platform-setting", "key": ALL}, "id"),
        prevent_initial_call=True,
    )
    def changed(values, ids):
        """Apply a safe change; stop and ask about a dangerous one."""
        from tradingagents.setup.settings import get_runtime_settings, setting

        triggered = getattr(ctx, "triggered_id", None)
        if not isinstance(triggered, dict):
            return no_update, no_update, no_update, no_update

        key = triggered.get("key")
        item = setting(key)
        if item is None:
            return no_update, no_update, no_update, no_update

        value = next(
            (
                value
                for identifier, value in zip(ids or [], values or [])
                if identifier.get("key") == key
            ),
            None,
        )
        config = current_config()
        if str(config.get(key, item.default)) == str(value):
            return no_update, no_update, no_update, no_update

        if item.is_dangerous_change(value):
            # Not a toggle. The consequence gets written out and a reason
            # gets recorded, because this is reachable from any browser
            # that can see the LXC.
            return True, confirmation_detail(item, value), {"key": key, "value": value}, no_update

        store = get_runtime_settings()
        if store is None:
            return no_update, no_update, no_update, dbc.Alert(
                "No database, so this cannot be saved. Set it in the "
                "environment and redeploy.",
                color="warning", className="py-2 mb-0",
            )
        try:
            store.set(key, value, actor="webui")
        except Exception as exc:
            return no_update, no_update, no_update, dbc.Alert(
                f"Unable to save: {exc}", color="danger", className="py-2 mb-0"
            )
        return no_update, no_update, no_update, dbc.Alert(
            f"{item.label} is now {value}.", color="success",
            className="py-2 mb-0",
        )

    @app.callback(
        Output("platform-confirm-modal", "is_open", allow_duplicate=True),
        Output("platform-settings-status", "children", allow_duplicate=True),
        Input("platform-confirm-accept", "n_clicks"),
        Input("platform-confirm-cancel", "n_clicks"),
        State("platform-pending-change", "data"),
        State("platform-confirm-reason", "value"),
        prevent_initial_call=True,
    )
    def confirm(_accept, _cancel, pending, reason):
        from tradingagents.setup.settings import get_runtime_settings, setting

        triggered = getattr(ctx, "triggered_id", None)
        if triggered == "platform-confirm-cancel" or not pending:
            # Re-rendering the body puts the control back where it was.
            return False, dbc.Alert(
                "Left as it was.", color="secondary", className="py-2 mb-0"
            )

        item = setting(pending.get("key"))
        store = get_runtime_settings()
        if item is None or store is None:
            return False, dbc.Alert(
                "Unable to save the change.", color="danger", className="py-2 mb-0"
            )
        try:
            store.set(
                item.key, pending.get("value"), actor="webui",
                reason=(reason or "").strip(),
            )
        except Exception as exc:
            return False, dbc.Alert(
                f"Unable to save: {exc}", color="danger", className="py-2 mb-0"
            )
        return False, dbc.Alert(
            [
                html.Strong(f"{item.label} changed. "),
                "The worker picks this up on its next cycle.",
            ],
            color="warning", className="py-2 mb-0",
        )


#: The editor's own id namespace. The wizard and this editor are mounted
#: at the same time — Bootstrap panes stay in the DOM — so they cannot
#: share field ids.
EDITOR_FIELD = "editor-credential"
EDITOR_PICKER = "editor-provider"


def register_role_editor_callbacks(app):
    """Configure or change any one provider, from the Set up list.

    Reachable for every requirement including the optional ones, which
    the wizard skips on purpose and which therefore had no route at all
    except a collapsed section of the integrations modal.
    """

    @app.callback(
        Output("role-editor-modal", "is_open"),
        Output("role-editor-title", "children"),
        Output("role-editor-body", "children"),
        Output("role-editor-target", "data"),
        Output("role-editor-status", "children"),
        Input({"type": "configure-role", "role": ALL}, "n_clicks"),
        Input("role-editor-cancel", "n_clicks"),
        prevent_initial_call=True,
    )
    def open_editor(clicks, _cancel):
        triggered = getattr(ctx, "triggered_id", None)
        if triggered == "role-editor-cancel":
            return False, no_update, no_update, no_update, no_update
        if not isinstance(triggered, dict):
            return (no_update,) * 5

        # The list re-renders on an interval, which recreates every button
        # with n_clicks back at zero. An all-zero fire is that re-render,
        # not somebody clicking.
        if not any(clicks or []):
            return (no_update,) * 5

        role_id = triggered.get("role")
        readiness = current_readiness()
        requirement = readiness.get(role_id) if readiness else None
        if requirement is None:
            return (no_update,) * 5

        role = get_role(role_id)
        chosen = selected(role_id, current_config()) if role else ""
        body = provider_form(
            requirement, role=role, chosen=chosen,
            id_type=EDITOR_FIELD, picker_type=EDITOR_PICKER, heading=False,
        )
        return True, requirement.title, body, role_id, ""

    @app.callback(
        Output("role-editor-status", "children", allow_duplicate=True),
        Output("wizard-save-status", "children", allow_duplicate=True),
        Input("role-editor-save", "n_clicks"),
        State("role-editor-target", "data"),
        State({"type": EDITOR_PICKER, "role": ALL}, "value"),
        State({"type": EDITOR_PICKER, "role": ALL}, "id"),
        State({"type": EDITOR_FIELD, "key": ALL}, "value"),
        State({"type": EDITOR_FIELD, "key": ALL}, "id"),
        prevent_initial_call=True,
    )
    def save_editor(_clicks, role_id, picker_values, picker_ids, values, ids):
        from tradingagents.setup.providers import role as find_role
        from tradingagents.setup.settings import get_runtime_settings

        notes = []

        # The provider choice first: the fields belong to whichever one is
        # selected, so storing them the other way round would file a
        # Tradier token under an Alpaca deployment.
        settings_store = get_runtime_settings()
        for identifier, value in zip(picker_ids or [], picker_values or []):
            role = find_role(identifier.get("role"))
            if role is None or not value or not role.setting:
                continue
            if str(current_config().get(role.setting) or "").lower() == str(value).lower():
                continue
            if settings_store is None:
                notes.append(
                    "The provider choice needs a database to be stored; "
                    "set it in the environment instead."
                )
                break
            try:
                settings_store.set(role.setting, value, actor="setup-editor")
                notes.append(f"{role.label} → {role.provider(value).label}")
            except Exception as exc:
                return _alert(f"Unable to record the choice: {exc}", "danger"), no_update

        stored = _store_credentials(values, ids)
        if isinstance(stored, str):
            return _alert(stored, "warning"), no_update
        if stored:
            notes.append(
                f"{stored} credential{'s' if stored != 1 else ''} saved"
            )

        if not notes:
            return _alert("Nothing changed.", "secondary"), no_update
        # Nudging the wizard's status also refreshes the readiness list.
        return _alert("; ".join(notes), "success"), "saved"

    @app.callback(
        Output("role-editor-status", "children", allow_duplicate=True),
        Output("wizard-save-status", "children", allow_duplicate=True),
        Input("role-editor-clear", "n_clicks"),
        State("role-editor-target", "data"),
        prevent_initial_call=True,
    )
    def clear_editor(_clicks, role_id):
        """Remove this provider's stored credentials.

        Blank means "keep" everywhere else, which is right for a form and
        useless for rotation — without this there is no way to take a key
        back out short of psql.
        """
        from tradingagents.integrations import get_integration_vault
        from tradingagents.setup.providers import role as find_role

        role = find_role(role_id)
        readiness = current_readiness()
        requirement = readiness.get(role_id) if readiness else None
        if requirement is None:
            return _alert("Nothing to remove.", "secondary"), no_update

        provider = (
            role.provider(selected(role_id, current_config())) if role else None
        )
        keys = [
            field.key
            for field in (provider.fields if provider else requirement.credentials)
            if field.type == "secret"
        ]
        if not keys:
            return _alert("This provider stores no credentials.", "secondary"), no_update

        vault = get_integration_vault()
        if vault is None:
            return _alert(
                "No encrypted vault is configured, so nothing is stored here "
                "to remove.", "warning",
            ), no_update
        try:
            removed = vault.delete_many(keys, actor="setup-editor")
        except Exception as exc:
            return _alert(f"Unable to remove: {exc}", "danger"), no_update

        return (
            _alert(
                f"Removed {removed} stored credential{'s' if removed != 1 else ''}. "
                "Any environment value still applies.",
                "success",
            ),
            "cleared",
        )


def _alert(message, colour):
    return dbc.Alert(message, color=colour, className="py-2 mb-0")


def _store_credentials(values, ids):
    """Save the non-blank secret fields; returns a count or a message."""
    from tradingagents.integrations import get_integration_vault

    entered = {
        identifier["key"]: value.strip()
        for identifier, value in zip(ids or [], values or [])
        if isinstance(value, str) and value.strip()
    }
    if not entered:
        return 0

    vault = get_integration_vault()
    if vault is None:
        return (
            "The encrypted vault is not configured, so there is nowhere to "
            "put these. Set INTEGRATION_VAULT_KEY and DATABASE_URL, or set "
            "the credentials in the environment."
        )
    return vault.set_many(entered, actor="setup-editor")

