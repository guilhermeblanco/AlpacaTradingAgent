"""Choosing a provider and filling in what it needs.

One editor, two places. The wizard walks you through the providers a
deployment cannot work without; the Set up list is how you reach any of
the others — including the optional ones the wizard deliberately skips,
which previously left them configurable only through a collapsed section
of the integrations modal.

`id_type` is why this is parameterised rather than copied. The wizard and
the Set up editor are both mounted at once — Bootstrap tab panes stay in
the DOM — so two copies of the same field would be two components with
the same id, which Dash refuses. Each caller names its own namespace.
"""

from dash import html
import dash_bootstrap_components as dbc


def credential_field(step_id, credential, source, *, id_type):
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
                id={"type": id_type, "key": credential.key},
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


def generic_field(step_id, field, source, *, id_type):
    """One field, rendered from its declared type.

    The renderer is generic on purpose. Providers are described in
    tradingagents/setup/providers.py and nothing here knows the name of a
    vendor, so adding one is a registration rather than another branch in
    a form.
    """
    if field.type == "secret":
        return credential_field(step_id, field, source, id_type=id_type)

    identifier = {"type": id_type, "key": field.key}
    if field.type == "bool":
        control = dbc.Switch(
            id=identifier, label="", value=bool(field.default), className="mt-1"
        )
    elif field.type == "choice":
        control = dbc.Select(
            id=identifier,
            options=[{"label": label, "value": value}
                     for value, label in field.choices],
            value=field.default,
        )
    else:
        control = dbc.Input(
            id=identifier, type="text",
            value=str(field.default) if field.default is not None else "",
            placeholder=field.placeholder, debounce=True,
        )

    return html.Div(
        [
            dbc.Label(field.label, className="mb-1"),
            control,
            html.Div(field.help, className="small text-muted mt-1")
            if field.help
            else None,
        ],
        className="mb-3",
        key=f"{step_id}-{field.key}",
    )


def provider_picker(role, chosen, *, id_type):
    """Choose the provider, then supply what it needs.

    The wizard used to state which provider was configured and ask for
    its key — "Model provider — OpenAI. This applies because
    llm_provider = openai." That is an accurate description of a decision
    nobody was offered. Choosing is the step.
    """
    if len(role.providers) == 1:
        only = role.providers[0]
        return html.Div(
            [
                html.Div(
                    [html.Strong(only.label), html.Span(f" — {only.blurb}",
                                                        className="text-muted")],
                    className="mb-1",
                ),
                html.Div(role.single_implementation_note,
                         className="small text-muted"),
            ],
            className="mb-3",
        )

    return html.Div(
        [
            dbc.RadioItems(
                id={"type": id_type, "role": role.id},
                options=[
                    {
                        "label": html.Span(
                            [
                                html.Strong(provider.label),
                                html.Span(f" — {provider.blurb}",
                                          className="text-muted ms-1"),
                            ]
                        ),
                        "value": provider.id,
                    }
                    for provider in role.providers
                ],
                value=chosen,
                className="wizard-provider-choice",
            ),
        ],
        className="mb-3",
    )


def provider_form(
    requirement,
    role=None,
    chosen="",
    *,
    id_type="wizard-credential",
    picker_type="wizard-provider",
    heading=True,
):
    """One requirement: pick the provider, then fill in its fields."""
    provider = role.provider(chosen) if role is not None else None
    fields = provider.fields if provider is not None else requirement.credentials

    return html.Div(
        [
            html.H5(role.label if role is not None else requirement.title,
                    className="mb-1")
            if heading
            else None,
            html.P(role.blurb if role is not None else requirement.why,
                   className="text-muted"),
            html.P(
                ["This applies because ", html.Code(requirement.because), "."],
                className="text-muted small",
            )
            if requirement.because and role is None
            else None,
            html.Hr(),
            provider_picker(role, chosen, id_type=picker_type)
            if role is not None
            else None,
            html.Div(
                [
                    generic_field(
                        requirement.id, field,
                        requirement.sources.get(field.key, ""),
                        id_type=id_type,
                    )
                    for field in fields
                ]
            ),
            html.Div(provider.note, className="small text-muted")
            if provider is not None and provider.note
            else None,
        ]
    )
