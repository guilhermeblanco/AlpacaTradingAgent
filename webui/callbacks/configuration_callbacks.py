"""Switching between configuration pages.

Show and hide rather than build on demand, for the same reason the stage
tabs keep every pane mounted: a callback whose Output is on another page
has to keep resolving, and the intervals that refresh the readiness list
and the settings panel have to keep ticking whichever page is in front.
"""

from dash import ALL, Input, Output, State, ctx

from webui.config.navigation import CONFIG_PAGES, DEFAULT_CONFIG_PAGE

VISIBLE: dict = {}
HIDDEN = {"display": "none"}


def register_configuration_callbacks(app):
    @app.callback(
        Output({"type": "config-page", "page": ALL}, "style"),
        Output({"type": "config-page-link", "page": ALL}, "active"),
        Output("config-page-store", "data"),
        Input({"type": "config-page-link", "page": ALL}, "n_clicks"),
        State("config-page-store", "data"),
    )
    def switch(_clicks, current):
        triggered = getattr(ctx, "triggered_id", None)
        selected = (
            triggered.get("page")
            if isinstance(triggered, dict)
            else (current or DEFAULT_CONFIG_PAGE)
        )
        if selected not in {page.id for page in CONFIG_PAGES}:
            selected = DEFAULT_CONFIG_PAGE

        styles = [
            dict(VISIBLE) if page.id == selected else dict(HIDDEN)
            for page in CONFIG_PAGES
        ]
        active = [page.id == selected for page in CONFIG_PAGES]
        return styles, active, selected
