"""The guided setup: one question at a time, and only the ones that apply.

The Integrations modal shows sixteen credentials at once and marks twelve
"Required". This asks for the two or three that this configuration actually
needs, in the order it makes sense to get them, and says what each one buys.

The steps come from `tradingagents.setup.readiness` rather than from a
script here, so a deployment with the macro analyst off is never asked for
a FRED key, and one already holding an Alpaca pair in the vault skips
straight past the broker. A wizard that walks you through things you do
not need is the laundry list with a progress bar on it.

The sequence is worked out when the wizard opens and then left alone.
Recomputing it as keys are saved sounds better and is wrong: completing a
step removes it from the list while the index has already moved on, so the
step after the one you just finished would be skipped. See the callbacks
module.

It opens by itself while setup is incomplete, because the alternative is
opening on a workbench of empty panels, each correctly reporting that
nothing has been recorded yet, none of which says the actual reason.
"""

from dash import dcc, html
import dash_bootstrap_components as dbc

#: The step after the requirements: nothing to fill in, just a plain
#: statement of what the thing is currently allowed to do. It earns its
#: place because "can this place an order?" is the one question worth
#: answering out loud before an operator walks away from it.
POSTURE_STEP = "posture"

WELCOME_STEP = "welcome"


def _credential_field(step_id, credential, source):
    configured = bool(source)
    return html.Div(
        [
            dbc.Label(
                [
                    credential.label,
                    dbc.Badge(
                        "already set", color="success", className="ms-2"
                    )
                    if configured
                    else None,
                ],
                className="mb-1",
            ),
            dbc.Input(
                id={"type": "wizard-credential", "key": credential.key},
                type="password",
                placeholder=(
                    "leave blank to keep the current value"
                    if configured
                    else credential.placeholder or "paste the key"
                ),
                autoComplete="off",
                debounce=True,
            ),
            html.Div(
                [
                    html.A(
                        "Where to get one",
                        href=credential.obtain_url,
                        target="_blank",
                        rel="noopener noreferrer",
                    ),
                    html.Span(
                        f" · reads from {credential.env_var} if you would rather "
                        "set it in the environment",
                        className="text-muted",
                    ),
                ],
                className="small mt-1",
            ),
        ],
        className="mb-3",
        key=f"{step_id}-{credential.key}",
    )


def requirement_step(requirement):
    """One requirement, asked for on its own."""
    return html.Div(
        [
            html.H5(requirement.title, className="mb-1"),
            html.P(requirement.why, className="text-muted"),
            html.P(
                ["This applies because ", html.Code(requirement.because), "."],
                className="text-muted small",
            )
            if requirement.because
            else None,
            html.Hr(),
            *(
                _credential_field(
                    requirement.id, credential, requirement.sources.get(credential.key, "")
                )
                for credential in requirement.credentials
            ),
        ]
    )


def welcome_step(readiness, steps):
    """What we are about to ask for, and what we are not."""
    if not steps:
        return html.Div(
            [
                html.H5("Nothing left to set up", className="mb-2"),
                html.P(
                    "Every credential this configuration needs is already "
                    "configured. Close this and start an analysis.",
                    className="text-muted",
                ),
            ]
        )

    return html.Div(
        [
            html.H5("Three things, usually", className="mb-2"),
            html.P(
                "A model provider, a broker, and one market-data key. That is "
                "the whole list for a default install — the Integrations "
                "screen shows sixteen because it shows every provider this "
                "build can talk to, not every provider you need.",
                className="text-muted",
            ),
            html.P(
                f"For this configuration, {len(steps)} "
                f"{'thing' if len(steps) == 1 else 'things'}:",
                className="mb-2",
            ),
            html.Ul(
                [
                    html.Li(
                        [
                            html.Strong(item.title),
                            html.Span(f" — {item.why}", className="text-muted"),
                        ],
                        className="mb-1",
                    )
                    for item in steps
                ]
            ),
            html.Hr(),
            html.P(
                [
                    "Keys are stored encrypted and never shown again. ",
                    html.Span(
                        "Nothing configured here can place an order.",
                        className="text-info",
                    ),
                ],
                className="small mb-0",
            ),
        ]
    )


def posture_step(config):
    """What it may do, stated plainly. No controls — see the module note."""
    autonomous = str(config.get("autonomous_enabled", False)).lower() in (
        "1", "true", "yes", "on",
    )
    gateway = str(config.get("execution_gateway") or "dry-run").strip().lower()
    paper = str(config.get("alpaca_use_paper", True)).lower() not in ("0", "false", "no")

    def line(label, value, safe, detail):
        return html.Div(
            [
                html.I(
                    className=(
                        "fas fa-shield-halved me-2 text-success"
                        if safe
                        else "fas fa-triangle-exclamation me-2 text-warning"
                    )
                ),
                html.Strong(f"{label}: "),
                html.Code(value),
                html.Div(detail, className="small text-muted ms-4"),
            ],
            className="mb-2",
        )

    return html.Div(
        [
            html.H5("What it may do", className="mb-1"),
            html.P(
                "Three independent switches, each defaulting to no. They live "
                "in the deployment's environment rather than here, because a "
                "setup wizard is the wrong place to arm a trading system.",
                className="text-muted",
            ),
            html.Hr(),
            line(
                "Autonomous worker",
                "enabled" if autonomous else "disabled",
                not autonomous,
                "AUTONOMOUS_ENABLED — whether anything runs without you clicking.",
            ),
            line(
                "Execution gateway",
                gateway,
                gateway != "broker",
                "EXECUTION_GATEWAY — dry-run prices and gates an intent, then "
                "does not send it.",
            ),
            line(
                "Alpaca account",
                "paper" if paper else "LIVE",
                paper,
                "ALPACA_USE_PAPER — which account an order would reach.",
            ),
        ]
    )


def create_setup_wizard():
    """The modal shell. Its body is rendered by the callbacks."""
    return dbc.Modal(
        [
            dbc.ModalHeader(
                dbc.ModalTitle(id="wizard-title"), close_button=True
            ),
            dbc.ModalBody(
                [
                    html.Div(id="wizard-progress", className="mb-3"),
                    html.Div(id="wizard-body"),
                    html.Div(id="wizard-save-status", className="mt-3"),
                ]
            ),
            dbc.ModalFooter(
                [
                    html.Div(id="wizard-step-count", className="me-auto small text-muted"),
                    dbc.Button(
                        "Back", id="wizard-back", color="secondary", outline=True,
                        className="me-2",
                    ),
                    dbc.Button("Next", id="wizard-next", color="primary"),
                ]
            ),
            # Which step we are on. Kept in the browser so a refresh
            # mid-setup does not start again from the top.
            dcc.Store(id="wizard-step", data=0, storage_type="session"),
            # The sequence, fixed when the wizard opens. Recomputing it as
            # credentials are saved would drop the step just completed and
            # silently skip the one after it — see the callbacks module.
            dcc.Store(id="wizard-plan", data=[], storage_type="session"),
            # Set once the operator closes the wizard, so it stops reopening
            # itself on every page load in a deployment they have decided to
            # leave half-configured.
            dcc.Store(id="wizard-dismissed", data=False, storage_type="session"),
        ],
        id="setup-wizard-modal",
        size="lg",
        is_open=False,
        backdrop="static",
        scrollable=True,
    )
