"""Wire the server-sent pulse to the panels that used to poll.

One clientside listener holds the EventSource and, on each pulse, bumps the
refresh interval's tick. Every existing consumer of `refresh-interval`
therefore gets push semantics without changing its own callback, and the
interval itself stays as a slow fallback for when a proxy eats the stream.
"""

from dash import Input, Output, clientside_callback, html

#: Opens the stream once and keeps it open. `set_props` lets one listener
#: drive components it is not an Output of, which is what makes the swap
#: from polling to push invisible to every other callback.
LISTENER = """
function(_children) {
    if (window.__tradingAgentsPulse) {
        return window.dash_clientside.no_update;
    }
    const bump = (payload) => {
        const clientside = window.dash_clientside;
        if (!clientside || !clientside.set_props) { return; }
        clientside.set_props('pulse-store', {data: payload});
        clientside.set_props('refresh-interval', {
            n_intervals: (payload && payload.revision) || 0,
        });
    };
    const connect = () => {
        const source = new EventSource('/stream/pulse');
        window.__tradingAgentsPulse = source;
        source.onmessage = (event) => {
            try { bump(JSON.parse(event.data)); } catch (error) { /* ignore */ }
        };
        source.onerror = () => {
            source.close();
            window.__tradingAgentsPulse = null;
            // The polling fallback keeps the UI alive while we retry.
            setTimeout(connect, 5000);
        };
    };
    connect();
    return window.dash_clientside.no_update;
}
"""


def register_pulse_callbacks(app):
    clientside_callback(
        LISTENER,
        Output("pulse-listener", "children"),
        Input("pulse-listener", "id"),
    )

    @app.callback(
        Output("refresh-status", "children"),
        Output("refresh-status", "className"),
        Input("pulse-store", "data"),
    )
    def report_stream_state(pulse):
        """What the old refresh governor used to say, without the polling."""
        if not pulse:
            return (
                "⏳ Waiting for the update stream",
                "text-secondary mt-2",
            )
        if pulse.get("analysis_running"):
            symbol = pulse.get("analyzing_symbol")
            return (
                f"🔄 Live — analyzing {symbol}" if symbol else "🔄 Live — analysis running",
                "text-success mt-2",
            )
        return "📡 Live — idle", "text-secondary mt-2"


def create_pulse_components():
    """The store the stream writes into, and the listener's mount point."""
    from dash import dcc

    return [
        dcc.Store(id="pulse-store"),
        html.Div(id="pulse-listener", style={"display": "none"}),
    ]
