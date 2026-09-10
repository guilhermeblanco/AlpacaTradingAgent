"""
webui/components/header.py - Header component for the web UI.
"""

import dash_bootstrap_components as dbc
from dash import dcc, html



def create_header():
    """Create the header component for the web UI."""
    return dbc.Card(
        dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    html.H1(
                        "AlpacaTradingAgent - Auditable Multi-Agent Trading Research Framework",
                        className="app-title mb-0"
                    )
                ], xs=12, md=9, className="d-flex align-items-center"),
                dbc.Col([
                    create_theme_toggle(),
                ], xs=12, md=3, className="app-header-actions d-flex align-items-center justify-content-end gap-2"),
            ], className="align-items-center g-3")
        ]),
        className="app-header mb-4"
    )


def create_theme_toggle():
    """Light or dark, remembered per browser.

    The store is what the server reads: a figure is rendered in Python,
    so it cannot pick up a CSS variable and has to be told which palette
    to draw in. Everything else follows the attribute the clientside
    callback sets, which is also what an asset applies before paint so
    there is no flash of the wrong theme on reload.
    """
    return html.Div(
        [
            dcc.Store(id="theme-store", storage_type="local", data="light"),
            dbc.Button(
                html.I(id="theme-toggle-icon", className="fas fa-moon"),
                id="theme-toggle",
                color="link",
                size="sm",
                className="theme-toggle",
                title="Switch between light and dark",
            ),
        ],
        className="d-inline-flex align-items-center",
    )

