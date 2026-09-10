"""Integrations: what is configured, what is answering, and adding more.

A list of instances grouped by what they do, each row saying whether it
is the one currently answering. Add creates another; the same editor the
wizard and the Set up list use fills it in.

The old screen showed sixteen credential fields, one per provider this
build can talk to, whether or not you used any of them. This shows what
*you* have configured — usually three or four rows — with the rest
behind Add.
"""

from dash import dcc, html
import dash_bootstrap_components as dbc

from tradingagents.setup.providers import ROLES

#: Kinds whose sources contribute together rather than compete. Their
#: rows get a checkbox and the heading says so, because "active" means
#: something different for them.
def is_exclusive(role) -> bool:
    return role.selection == "one"


def instance_row(role, instance):
    """One configured integration."""
    exclusive = is_exclusive(role)
    return html.Div(
        [
            html.Div(
                [
                    html.I(
                        className=(
                            "fas fa-circle-check me-2 text-success"
                            if instance.active
                            else "far fa-circle me-2 text-muted"
                        )
                    ),
                    html.Strong(instance.label, className="me-2"),
                    dbc.Badge("active", color="success", className="me-2")
                    if instance.active
                    else None,
                    dbc.Badge(
                        f"{len(instance.configured_fields)} stored",
                        color="secondary",
                        className="me-2",
                    )
                    if instance.configured_fields
                    else dbc.Badge("no credentials", color="warning", className="me-2"),
                ],
                className="d-flex align-items-center flex-wrap",
            ),
            html.Div(
                [
                    dbc.Button(
                        "Use this" if exclusive else "Enable",
                        id={"type": "integration-activate", "id": instance.id},
                        color="link",
                        size="sm",
                        className="p-0 me-3",
                        disabled=instance.active and exclusive,
                    ),
                    dbc.Button(
                        "Turn off",
                        id={"type": "integration-deactivate", "id": instance.id},
                        color="link",
                        size="sm",
                        className="p-0 me-3",
                        disabled=not instance.active,
                    ),
                    dbc.Button(
                        "Edit",
                        id={"type": "integration-edit", "id": instance.id},
                        color="link",
                        size="sm",
                        className="p-0 me-3",
                    ),
                    dbc.Button(
                        "Remove",
                        id={"type": "integration-remove", "id": instance.id},
                        color="link",
                        size="sm",
                        className="p-0 text-danger",
                    ),
                ],
                className="small",
            ),
        ],
        className="integration-row",
    )


def role_section(role, instances):
    """One kind, its instances, and a way to add another."""
    exclusive = is_exclusive(role)
    heading = html.Div(
        [
            html.Div(
                [
                    html.Strong(role.label.upper(), className="integration-kind"),
                    html.Span(
                        "one active" if exclusive else "several may be active",
                        className="text-muted small ms-2",
                    ),
                ]
            ),
            dbc.Button(
                [html.I(className="fas fa-plus me-1"), "Add"],
                id={"type": "integration-add", "role": role.id},
                color="link",
                size="sm",
                className="p-0",
            ),
        ],
        className="d-flex justify-content-between align-items-center mt-3 mb-1",
    )

    if not instances:
        body = html.Div(
            [
                html.Span("Nothing configured. ", className="text-muted small"),
                html.Span(role.blurb, className="text-muted small"),
            ],
            className="py-2",
        )
    else:
        body = html.Div([instance_row(role, item) for item in instances])

    return html.Div([heading, body], className="integration-section")


def integrations_body(by_kind, unavailable=""):
    """Every kind, in the order they matter."""
    if unavailable:
        return dbc.Alert(unavailable, color="warning", className="py-2 mb-0")
    return html.Div(
        [role_section(role, by_kind.get(role.id, [])) for role in ROLES]
    )


def create_integrations_modal():
    """The screen behind the header button."""
    return dbc.Modal(
        [
            dbc.ModalHeader(dbc.ModalTitle("Integrations")),
            dbc.ModalBody(
                [
                    html.P(
                        "What this deployment is configured to talk to. Several "
                        "of a kind can exist — a paper broker and a live one, a "
                        "production model key and an evaluation one — and the "
                        "active one is what everything else uses.",
                        className="text-muted small",
                    ),
                    html.Div(id="integrations-body"),
                    html.Div(id="integrations-status", className="mt-2"),
                    dcc.Interval(id="integrations-interval", interval=30_000),
                ]
            ),
            dbc.ModalFooter(
                dbc.Button(
                    "Close", id="integrations-close", color="secondary", outline=True
                )
            ),
            integration_editor_modal(),
        ],
        id="integrations-modal",
        is_open=False,
        size="lg",
        scrollable=True,
    )


def integration_editor_modal():
    """Add one, or change one that exists."""
    return dbc.Modal(
        [
            dbc.ModalHeader(dbc.ModalTitle(id="integration-editor-title")),
            dbc.ModalBody(
                [
                    dbc.Label("Name", className="mb-1"),
                    dbc.Input(
                        id="integration-editor-name",
                        placeholder="paper, production, evaluation…",
                        debounce=True,
                        className="mb-1",
                    ),
                    html.Div(
                        "What you will recognise it by in the list. Optional.",
                        className="small text-muted mb-3",
                    ),
                    html.Div(id="integration-editor-body"),
                    html.Div(id="integration-editor-status", className="mt-2"),
                ]
            ),
            dbc.ModalFooter(
                [
                    dbc.Button(
                        "Cancel", id="integration-editor-cancel",
                        color="secondary", outline=True, className="me-2",
                    ),
                    dbc.Button(
                        "Save and activate",
                        id="integration-editor-save",
                        color="primary",
                    ),
                ]
            ),
            # Which kind is being added, or which instance is being
            # edited. One store rather than two, because they are the
            # same screen doing the same thing.
            dcc.Store(id="integration-editor-target"),
        ],
        id="integration-editor-modal",
        is_open=False,
        size="lg",
        scrollable=True,
    )
