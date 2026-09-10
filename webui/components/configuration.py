"""The Configuration stage: pages down the side, sections across the top.

Settings used to be scattered — some in the run form, some in a modal,
some in a panel at the bottom of a tab whose job was watching things
happen. This is the one place they live, and it is organised the way
somebody looking for one thinks: which part of the machine, then which
aspect of it.

Pages are the parts. Sections are tabs within a page, because "the
symbols we track" and "which model runs which phase" are both Analysis
and neither belongs in a flat list with the other.

Everything is rendered up front and hidden with CSS, for the same reason
the stage tabs are: a callback whose Output is on another page has to
keep resolving, and an interval on one has to keep ticking.
"""

from dash import dcc, html
import dash_bootstrap_components as dbc

from webui.config.navigation import CONFIG_PAGES, DEFAULT_CONFIG_PAGE


def page_nav():
    """The list of pages, down the side."""
    return dbc.Nav(
        [
            dbc.NavLink(
                page.label,
                id={"type": "config-page-link", "page": page.id},
                href="#",
                active=page.id == DEFAULT_CONFIG_PAGE,
                className="config-page-link",
            )
            for page in CONFIG_PAGES
        ],
        vertical=True,
        pills=True,
        className="config-nav",
    )


def page_body(page, panels):
    """One page: its sections as tabs, or just its content if it has one."""
    if len(page.sections) == 1:
        _label, name = page.sections[0]
        body = panels[name]() if name in panels else html.Div()
    else:
        body = dbc.Tabs(
            [
                dbc.Tab(
                    panels[name]() if name in panels else html.Div(),
                    label=label,
                    tab_id=f"{page.id}-{name}",
                )
                for label, name in page.sections
            ],
            className="config-section-tabs",
        )

    return html.Div(
        [
            html.H5(page.label, className="mb-1"),
            html.P(page.blurb, className="text-muted small mb-3"),
            body,
        ],
        id={"type": "config-page", "page": page.id},
        className="config-page",
        style={} if page.id == DEFAULT_CONFIG_PAGE else {"display": "none"},
    )


def create_configuration(panels):
    """The whole stage.

    `panels` is the layout's panel table rather than an import, so this
    module describes an arrangement without also being a second list of
    what exists.
    """
    return html.Div(
        [
            dcc.Store(id="config-page-store", data=DEFAULT_CONFIG_PAGE),
            dbc.Row(
                [
                    dbc.Col(page_nav(), md=3, lg=2, className="config-nav-column"),
                    dbc.Col(
                        [page_body(page, panels) for page in CONFIG_PAGES],
                        md=9,
                        lg=10,
                        className="config-body-column",
                    ),
                ],
                className="g-3",
            ),
        ],
        className="configuration",
    )
