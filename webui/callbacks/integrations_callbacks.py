"""Callbacks for the Integrations screen.

Adding and editing are one screen because they are one operation with a
different starting point: pick a provider, name it, fill in its fields.
The form is the same one the wizard uses, from
`webui/components/provider_editor.py`, so a field looks and behaves the
same wherever you meet it.
"""

from dash import ALL, Input, Output, State, ctx, html, no_update
import dash_bootstrap_components as dbc

from tradingagents.setup.providers import ROLES, role as get_role
from webui.components.integrations_panel import integrations_body
from webui.components.provider_editor import provider_form

#: This screen's own id namespace. Every editor in the app is mounted at
#: once — Bootstrap panes stay in the DOM — so each needs its own.
FIELD = "integration-field"
PICKER = "integration-picker"


def _store():
    from tradingagents.integrations import get_integration_store

    return get_integration_store()


def _grouped():
    """Instances by kind, or a sentence saying why there are none."""
    store = _store()
    if store is None:
        return {}, (
            "Integrations are stored in PostgreSQL. Without DATABASE_URL "
            "there is nowhere to keep them, and credentials have to be set "
            "in the environment instead."
        )
    try:
        by_kind: dict[str, list] = {}
        for instance in store.list():
            by_kind.setdefault(instance.kind, []).append(instance)
        return by_kind, ""
    except Exception as exc:
        return {}, f"Unable to read integrations: {exc}"


def _alert(message, colour):
    return dbc.Alert(message, color=colour, className="py-2 mb-0")


def _editor_form(role, provider_id, requirement=None):
    """The provider picker and fields, in this screen's namespace."""
    from tradingagents.setup import evaluate_readiness

    if requirement is None:
        try:
            requirement = evaluate_readiness().get(role.id)
        except Exception:
            requirement = None
    if requirement is None:
        # A kind with no requirement of its own still has providers and
        # fields; synthesise the shell the form needs.
        from tradingagents.setup.readiness import Requirement

        requirement = Requirement(
            id=role.id, title=role.label, why=role.blurb, level="optional"
        )
    return provider_form(
        requirement,
        role=role,
        chosen=provider_id,
        id_type=FIELD,
        picker_type=PICKER,
        heading=False,
    )


def register_integrations_callbacks(app):
    # No opener: this is a page under Configuration now, not a modal
    # behind a button in the corner. One place for settings, one way in.
    @app.callback(
        Output("integrations-body", "children"),
        Input("integrations-interval", "n_intervals"),
        Input("integrations-status", "children"),
    )
    def render(_intervals, _status):
        by_kind, unavailable = _grouped()
        return integrations_body(by_kind, unavailable)

    # ── Activation ───────────────────────────────────────────────────
    @app.callback(
        Output("integrations-status", "children"),
        Input({"type": "integration-activate", "id": ALL}, "n_clicks"),
        Input({"type": "integration-deactivate", "id": ALL}, "n_clicks"),
        Input({"type": "integration-remove", "id": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def act(activate_clicks, deactivate_clicks, remove_clicks):
        triggered = getattr(ctx, "triggered_id", None)
        if not isinstance(triggered, dict):
            return no_update
        # The list re-renders on an interval, which recreates every
        # button with n_clicks back at zero.
        if not any(
            (activate_clicks or []) + (deactivate_clicks or []) + (remove_clicks or [])
        ):
            return no_update

        store = _store()
        if store is None:
            return _alert("No database, so integrations cannot be changed.", "warning")

        action = triggered.get("type")
        instance_id = triggered.get("id")
        instance = store.get(instance_id)
        if instance is None:
            return _alert("That integration is already gone.", "secondary")

        try:
            if action == "integration-activate":
                store.activate(instance_id, actor="webui")
                return _alert(f"{instance.label} is now active.", "success")
            if action == "integration-deactivate":
                store.deactivate(instance_id, actor="webui")
                return _alert(f"{instance.label} turned off.", "secondary")
            store.remove(instance_id, actor="webui")
            return _alert(
                f"{instance.label} removed, along with its stored credentials.",
                "secondary",
            )
        except Exception as exc:
            return _alert(f"Unable to change it: {exc}", "danger")

    # ── Adding and editing ───────────────────────────────────────────
    @app.callback(
        Output("integration-editor-modal", "is_open"),
        Output("integration-editor-title", "children"),
        Output("integration-editor-body", "children"),
        Output("integration-editor-name", "value"),
        Output("integration-editor-target", "data"),
        Output("integration-editor-status", "children"),
        Input({"type": "integration-add", "role": ALL}, "n_clicks"),
        Input({"type": "integration-edit", "id": ALL}, "n_clicks"),
        Input("integration-editor-cancel", "n_clicks"),
        prevent_initial_call=True,
    )
    def open_editor(add_clicks, edit_clicks, _cancel):
        triggered = getattr(ctx, "triggered_id", None)
        if triggered == "integration-editor-cancel":
            return False, no_update, no_update, no_update, no_update, no_update
        if not isinstance(triggered, dict):
            return (no_update,) * 6
        if not any((add_clicks or []) + (edit_clicks or [])):
            return (no_update,) * 6

        if triggered.get("type") == "integration-add":
            role = get_role(triggered.get("role"))
            if role is None:
                return (no_update,) * 6
            return (
                True,
                f"Add {role.label.lower()}",
                _editor_form(role, role.default or role.ids[0]),
                "",
                {"mode": "add", "kind": role.id},
                "",
            )

        store = _store()
        instance = store.get(triggered.get("id")) if store else None
        if instance is None:
            return (no_update,) * 6
        role = get_role(instance.kind)
        return (
            True,
            f"Edit {instance.label}",
            _editor_form(role, instance.provider),
            instance.name,
            {"mode": "edit", "id": instance.id, "kind": instance.kind},
            "",
        )

    @app.callback(
        Output("integration-editor-status", "children", allow_duplicate=True),
        Output("integration-editor-modal", "is_open", allow_duplicate=True),
        Output("integrations-status", "children", allow_duplicate=True),
        Input("integration-editor-save", "n_clicks"),
        State("integration-editor-target", "data"),
        State("integration-editor-name", "value"),
        State({"type": PICKER, "role": ALL}, "value"),
        State({"type": FIELD, "key": ALL}, "value"),
        State({"type": FIELD, "key": ALL}, "id"),
        prevent_initial_call=True,
    )
    def save(_clicks, target, name, picker_values, values, ids):
        if not target:
            return no_update, no_update, no_update

        store = _store()
        if store is None:
            return (
                _alert(
                    "Integrations are stored in PostgreSQL, and there is no "
                    "DATABASE_URL. Set the credentials in the environment.",
                    "warning",
                ),
                no_update,
                no_update,
            )

        provider = next((value for value in (picker_values or []) if value), None)
        credentials = {
            identifier["key"]: value.strip()
            for identifier, value in zip(ids or [], values or [])
            if isinstance(value, str) and value.strip()
        }

        try:
            if target.get("mode") == "add":
                role = get_role(target.get("kind"))
                instance = store.add(
                    target["kind"],
                    provider or (role.default if role else ""),
                    name or "",
                    credentials=credentials,
                    activate=True,
                    actor="webui",
                )
                message = f"Added {instance.label}."
            else:
                instance_id = target["id"]
                if name is not None:
                    store.rename(instance_id, name, actor="webui")
                if credentials:
                    store.set_credentials(instance_id, credentials, actor="webui")
                store.activate(instance_id, actor="webui")
                message = f"Saved {store.get(instance_id).label}."
        except Exception as exc:
            return _alert(f"Unable to save: {exc}", "danger"), no_update, no_update

        return "", False, _alert(message, "success")
