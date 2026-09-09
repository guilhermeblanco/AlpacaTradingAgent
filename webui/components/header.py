"""
webui/components/header.py - Header component for the web UI.
"""

import dash_bootstrap_components as dbc
from dash import html

from webui.components.api_config_modal import create_config_button


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
                    create_config_button()
                ], xs=12, md=3, className="app-header-actions d-flex align-items-center justify-content-end"),
            ], className="align-items-center g-3")
        ]),
        className="app-header mb-4"
    ) 
