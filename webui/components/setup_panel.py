"""What this deployment still needs, said once and in order.

The Integrations modal lists sixteen credentials and calls twelve of them
required, which is how a three-key setup reads as a wall. This panel asks
`tradingagents.setup.readiness` instead, so it shows the requirements that
actually apply to *this* configuration — and, for the ones that apply only
because of a choice, says which choice.

The wizard is the front door; this list is what it is walking you through,
kept visible so it is possible to see the whole shape rather than one step
at a time.
"""

from dash import dcc, html
import dash_bootstrap_components as dbc

LEVEL_STYLE = {
    "required": ("danger", "Required"),
    "conditional": ("warning", "Required by a setting"),
    "recommended": ("info", "Recommended"),
    "optional": ("secondary", "Optional"),
}

#: Where a configured value came from. Worth showing: "it works on my
#: machine" and "it is in the vault" are different states, and only one of
#: them survives a redeploy.
SOURCE_LABEL = {
    "vault": "encrypted vault",
    "environment": "environment",
    "session": "this session only",
    "config": "config default",
}


def requirement_row(requirement):
    """One requirement: what it is, whether it is done, and why it matters."""
    colour, level_label = LEVEL_STYLE.get(
        requirement.level, ("secondary", requirement.level)
    )
    done = requirement.satisfied

    credentials = []
    for credential in requirement.credentials:
        source = requirement.sources.get(credential.key, "")
        credentials.append(
            html.Li(
                [
                    html.Span(credential.label, className="me-2"),
                    dbc.Badge(
                        SOURCE_LABEL.get(source, source) if source else "not set",
                        color="success" if source else "secondary",
                        className="me-2",
                    ),
                    html.A(
                        "get a key",
                        href=credential.obtain_url,
                        target="_blank",
                        rel="noopener noreferrer",
                        className="small",
                    )
                    if credential.obtain_url and not source
                    else None,
                ],
                className="small",
            )
        )

    return html.Div(
        [
            html.Div(
                [
                    html.I(
                        className=(
                            "fas fa-check-circle me-2 text-success"
                            if done
                            else "far fa-circle me-2 text-muted"
                        )
                    ),
                    html.Strong(requirement.title, className="me-2"),
                    dbc.Badge(level_label, color=colour, className="me-2"),
                ],
                className="d-flex align-items-center flex-wrap",
            ),
            html.Div(requirement.why, className="small text-muted mt-1"),
            # Naming the setting turns "you must do this" into "you asked
            # for this", which is the difference between a requirement a
            # reader can act on and one they can only obey.
            html.Div(
                ["Applies because ", html.Code(requirement.because)],
                className="small text-muted",
            )
            if requirement.because
            else None,
            html.Ul(credentials, className="mb-0 mt-1") if credentials else None,
        ],
        className="setup-requirement",
    )


def readiness_summary(readiness):
    """The list, most urgent first, with a one-line verdict on top."""
    if readiness is None:
        return dbc.Alert(
            "Unable to read the configuration.", color="warning", className="py-2 mb-0"
        )

    blocking = readiness.blocking
    if blocking:
        verdict = dbc.Alert(
            [
                html.Strong(
                    f"{len(blocking)} thing{'s' if len(blocking) > 1 else ''} "
                    "still needed before an analysis can run."
                ),
                html.Div(
                    ", ".join(item.title for item in blocking), className="small mt-1"
                ),
            ],
            color="warning",
            className="py-2",
        )
    else:
        verdict = dbc.Alert(
            "Everything required is configured.", color="success", className="py-2"
        )

    ordered = sorted(
        readiness.requirements,
        key=lambda item: (item.satisfied, item.order),
    )

    return html.Div(
        [
            verdict,
            html.Div([requirement_row(item) for item in ordered]),
            html.Hr(),
            html.Div(
                [html.Div(note, className="small text-muted mb-1") for note in readiness.notes],
            )
            if readiness.notes
            else None,
        ]
    )


def create_setup_panel():
    """The Set up stage."""
    return dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        html.H4("Set up", className="mb-1"),
                        html.P(
                            "Three things are usually enough: a model provider, "
                            "a broker, and one market-data key. What follows is "
                            "what this configuration actually needs.",
                            className="text-muted small mb-0",
                        ),
                    ],
                    className="mb-3",
                ),
                dbc.Button(
                    [html.I(className="fas fa-magic me-2"), "Run guided setup"],
                    id="open-setup-wizard-btn",
                    color="primary",
                    className="me-2 mb-3",
                ),
                dbc.Button(
                    [html.I(className="fas fa-key me-2"), "All integrations"],
                    id="open-api-config-from-setup-btn",
                    color="outline-secondary",
                    className="mb-3",
                ),
                dcc.Interval(id="setup-readiness-interval", interval=30_000),
                html.Div(id="setup-readiness"),
            ]
        ),
        className="mb-4",
    )
