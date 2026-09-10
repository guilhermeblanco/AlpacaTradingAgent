"""Settings an operator can change while the system is running.

Everything here writes to the runtime settings store, which outranks the
environment, so a change takes effect without a redeploy. That was the
requirement; the shape of this panel is what makes it survivable.

Three switches can let the system do more than it could a moment ago:
enabling the autonomous worker, leaving dry-run, and moving off the paper
account. Those are not toggles. They are a confirmation with the
consequence written out and a reason recorded, because a browser with no
authentication in front of it can now reach them.

Calming something down is never gated. Turning the worker off, going back
to dry-run, returning to paper — one click, no confirmation, no reason.
Making the safe direction slower than the dangerous one is how safety
features end up disabled.
"""

from dash import dcc, html
import dash_bootstrap_components as dbc

from tradingagents.setup.settings import SETTINGS, setting

#: Which settings appear where. Ordering by consequence rather than by
#: alphabet: the three that decide whether real orders can happen sit
#: together at the top, under their own heading.
ARMING = ("autonomous_enabled", "execution_gateway", "alpaca_use_paper")

BEHAVIOUR = (
    "autonomous_asset_filter",
    "autonomous_interval_seconds",
    "autonomous_requested_notional_usd",
    "require_point_in_time_web_search",
)

#: What it may spend and how hard it may push. Grouped apart from
#: behaviour because these are the numbers you reach for when the bill
#: or the rate limit is the problem, and they were previously reachable
#: only by editing source.
LIMITS = (
    "daily_llm_token_budget",
    "autonomous_max_concurrency",
    "autonomous_provider_concurrency",
    "autonomous_max_candidates",
    "evaluation_worker_interval_seconds",
)

PROVIDERS = ("llm_provider", "execution_broker", "research_market_data_provider")


def _control(item, value):
    identifier = {"type": "platform-setting", "key": item.key}

    if item.type == "bool":
        return dbc.Switch(id=identifier, label="", value=bool(value))
    if item.type == "choice" and item.choices:
        return dbc.Select(
            id=identifier,
            options=[{"label": label, "value": str(choice)}
                     for choice, label in item.choices],
            value=str(value),
        )
    if item.key in PROVIDERS:
        from tradingagents.setup.providers import ROLES

        role = next((one for one in ROLES if one.setting == item.key), None)
        options = (
            [{"label": provider.label, "value": provider.id}
             for provider in role.providers]
            if role
            else []
        )
        return dbc.Select(id=identifier, options=options, value=str(value))
    return dbc.Input(id=identifier, type="text", value=str(value), debounce=True)


def setting_row(item, value, source=""):
    """One setting: what it is, what it is now, and where that came from."""
    return html.Div(
        [
            dbc.Row(
                [
                    dbc.Col(
                        [
                            html.Div(
                                [
                                    html.Strong(item.label, className="me-2"),
                                    dbc.Badge(
                                        "arms the system",
                                        color="warning",
                                        className="me-2",
                                    )
                                    if item.dangerous
                                    else None,
                                    dbc.Badge(source, color="secondary")
                                    if source
                                    else None,
                                ],
                                className="d-flex align-items-center flex-wrap",
                            ),
                            html.Div(item.blurb, className="small text-muted"),
                        ],
                        md=7,
                    ),
                    dbc.Col(_control(item, value), md=5),
                ],
                className="align-items-center g-2",
            ),
        ],
        className="platform-setting",
    )


def create_platform_settings():
    """The Set up stage's second half: what it may do, and how it behaves."""
    return dbc.Card(
        dbc.CardBody(
            [
                html.H5("Platform settings", className="mb-1"),
                html.P(
                    "Changes take effect without a redeploy — the worker "
                    "re-reads these between cycles. Every change is recorded "
                    "with who made it.",
                    className="text-muted small",
                ),
                html.Div(id="platform-settings-body"),
                dcc.Interval(id="platform-settings-interval", interval=30_000),
                html.Div(id="platform-settings-status", className="mt-2"),
                html.Hr(),
                html.Details(
                    [
                        html.Summary("Recent changes", className="small"),
                        html.Div(id="platform-settings-history", className="mt-2"),
                    ]
                ),
                _confirm_modal(),
            ]
        ),
        className="mb-4",
    )


def _confirm_modal():
    """The gate in front of the three switches that arm something."""
    return dbc.Modal(
        [
            dbc.ModalHeader(dbc.ModalTitle("Confirm")),
            dbc.ModalBody(
                [
                    html.Div(id="platform-confirm-detail"),
                    html.Hr(),
                    dbc.Label("Why? Recorded with the change.", className="small"),
                    dbc.Input(
                        id="platform-confirm-reason",
                        placeholder="e.g. paper results look right after two weeks",
                        debounce=True,
                    ),
                ]
            ),
            dbc.ModalFooter(
                [
                    dbc.Button(
                        "Cancel", id="platform-confirm-cancel",
                        color="secondary", outline=True, className="me-2",
                    ),
                    dbc.Button(
                        "Yes, change it", id="platform-confirm-accept",
                        color="warning",
                    ),
                ]
            ),
            dcc.Store(id="platform-pending-change"),
        ],
        id="platform-confirm-modal",
        is_open=False,
        backdrop="static",
    )


def settings_body(config, sources=None):
    """Every setting, grouped by what it does to you."""
    sources = sources or {}

    def group(title, keys, note=""):
        rows = []
        for key in keys:
            item = setting(key)
            if item is None:
                continue
            rows.append(setting_row(item, config.get(key, item.default),
                                    sources.get(key, "")))
        if not rows:
            return None
        return html.Div(
            [
                html.H6(title, className="mt-3 mb-1"),
                html.Div(note, className="small text-muted mb-2") if note else None,
                *rows,
            ]
        )

    return html.Div(
        [
            group(
                "What it may do",
                ARMING,
                "Each of these asks for confirmation and a reason before it "
                "lets the system do more. Turning one back down does not.",
            ),
            group("Providers", PROVIDERS),
            group(
                "Limits",
                LIMITS,
                "What it may spend, and how hard it may push a provider.",
            ),
            group("Behaviour", BEHAVIOUR),
        ]
    )


def confirmation_detail(item, value):
    """What is about to change, in the plainest words available."""
    label = next(
        (text for choice, text in item.choices if str(choice) == str(value)),
        str(value),
    )
    consequences = {
        "autonomous_enabled": (
            "The worker will start scanning and deciding on its own, on its "
            "configured interval, with nobody watching."
        ),
        "execution_gateway": (
            "Intents that pass every gate will be sent to the broker rather "
            "than stopped at the last step."
        ),
        "alpaca_use_paper": (
            "Orders will reach the live account. Real money, real positions."
        ),
    }
    return html.Div(
        [
            html.P([html.Strong(item.label), " → ", html.Code(label)]),
            html.P(consequences.get(item.key, item.blurb), className="text-warning"),
            html.P(
                "The other two switches still apply independently.",
                className="small text-muted mb-0",
            ),
        ]
    )
