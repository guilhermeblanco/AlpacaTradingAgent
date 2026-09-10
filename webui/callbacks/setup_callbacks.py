"""Callbacks for the Set up stage.

Reading readiness is cheap — it asks each layer whether a credential is
configured, never what it is — so the panel can simply recompute on an
interval rather than needing anything to tell it that a key was saved.
"""

from dash import Input, Output, State, no_update

from webui.components.setup_panel import readiness_summary


def current_readiness():
    """Readiness for the running configuration, or None if unreadable."""
    from tradingagents.dataflows.config import get_config
    from tradingagents.setup import evaluate_readiness

    try:
        return evaluate_readiness(get_config())
    except Exception:
        try:
            return evaluate_readiness({})
        except Exception:
            return None


def register_setup_callbacks(app):
    @app.callback(
        Output("setup-readiness", "children"),
        Input("setup-readiness-interval", "n_intervals"),
        Input("api-config-modal", "is_open"),
    )
    def show_readiness(_intervals, _modal_open):
        # Also fires when the integrations modal closes, which is the moment
        # a key is most likely to have just been saved.
        return readiness_summary(current_readiness())

    @app.callback(
        Output("api-config-modal", "is_open", allow_duplicate=True),
        Input("open-api-config-from-setup-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def open_integrations(n_clicks):
        return True if n_clicks else no_update
