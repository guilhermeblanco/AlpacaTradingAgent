"""
app_dash.py - Simplified Dash-based web UI for TradingAgents

This is the refactored version of app_dash.py that uses organized modules
for better code structure and maintainability.

Symbols are stored in browser storage so a page refresh restores every
symbol page rather than only the first, and the pagination callbacks guard
against an index left pointing past the end of a shortened symbol list.
"""

import dash
import dash_bootstrap_components as dbc
from flask import Flask
import logging

from webui.config.constants import APP_CONFIG, COLORS
from webui.layout import create_main_layout
from webui.callbacks import register_all_callbacks


def create_app():
    """Create and configure the Dash application"""

    # Initialize Flask server
    server = Flask(__name__)

    # One long-lived stream per tab replaces per-callback polling; see
    # webui/utils/pulse.py.
    from webui.utils.health import register_health_route
    from webui.utils.pulse import register_pulse_route
    from webui.utils.state import app_state

    register_pulse_route(server, app_state)

    # Liveness for the container runtime; see webui/utils/health.py for why it
    # deliberately checks nothing but the process itself.
    register_health_route(server)

    # Initialize Dash app with Bootstrap
    app = dash.Dash(
        __name__,
        server=server,
        external_stylesheets=[
            # Plain Bootstrap, not a Bootswatch theme.
            #
            # DARKLY is a *dark* stylesheet: it hardcodes light text on
            # every Bootstrap component, so the light palette produced
            # white text on a white page. Bootstrap 5.3 has colour modes
            # of its own, driven by `data-bs-theme`, so the base can be
            # neutral and follow the toggle like everything else.
            dbc.themes.BOOTSTRAP,
            *APP_CONFIG["external_stylesheets"]
        ],
        suppress_callback_exceptions=APP_CONFIG["suppress_callback_exceptions"],
        update_title=APP_CONFIG["update_title"],
    )

    # Set app title
    app.title = APP_CONFIG["title"]

    # Serve the generated stylesheet.
    #
    # webui/utils/styles.py holds the design tokens and every rule the
    # workbench depends on — board cards, the vitals strip, the decision
    # tape — and nothing imported it, so none of it ever reached a browser.
    # The tests passed because they asserted against the string rather than
    # against anything served, which is the same mistake that left a
    # clientside callback unattached. Injected here rather than dropped into
    # assets/ because the token block is generated from Python and there
    # should stay exactly one definition of it.
    app.index_string = _index_string()

    # Set the layout
    app.layout = create_main_layout()

    # Register all callbacks
    register_all_callbacks(app)

    return app


def _index_string() -> str:
    """Dash's default index, plus the generated stylesheet.

    Placed after `{%css%}` so that where a rule here and one in
    assets/custom.css collide, this one wins — it is the newer of the two
    and the one built against the design tokens.
    """
    from webui.utils.styles import CSS

    return """<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>{%title%}</title>
        {%favicon%}
        {%css%}
        <style>
""" + CSS + """
        </style>
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>"""


def run_app(port=7860, share=False, server_name="127.0.0.1", debug=False, max_threads=1):
    """Run the TradingAgents Dash Web UI"""
    
    # Create the app
    app = create_app()
    
    if debug:
        print(f"Starting TradingAgents Dash Web UI on port {port}...")
    else:
        print("Starting TradingAgents Web UI...")
    
    # Suppress verbose HTTP request logs from Werkzeug
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    # Optionally also silence Dash's callback exceptions logger
    logging.getLogger("dash.callback").setLevel(logging.ERROR)
    
    # Run the app
    app.run(
        port=port,
        host=server_name,
        debug=debug,
        dev_tools_hot_reload=debug,
        use_reloader=False  # Disable reloader to prevent double-start in debug mode
    )
    
    return 0


# The app instance is created lazily: building it eagerly at import time
# fetches Alpaca account data, so any import of this module (directly or via
# the webui package) would trigger network calls.
_app = None


def __getattr__(name):
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    run_app(debug=True) 