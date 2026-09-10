"""Light or dark, and getting the answer to the parts that need it.

Two consumers, and they need it in different forms.

*The browser* wants an attribute on the document element, which CSS then
matches. That is a clientside callback, because a round trip to the
server to change a colour would be visible.

*The server* wants a string, because a Plotly figure is rendered in
Python and cannot read a CSS variable. Figures are the one thing that
does not follow the attribute for free, so the store is an Input to
every callback that builds one.
"""

from dash import Input, Output, State

#: Flip the attribute, remember it, and report back what it now is. The
#: helpers come from assets/00_theme.js, which also applies the
#: remembered value before the page paints.
TOGGLE = """
function (clicks, current) {
    if (!clicks) {
        return [window.tradingagentsTheme(), window.dash_clientside.no_update];
    }
    var next = current === 'dark' ? 'light' : 'dark';
    window.tradingagentsSetTheme(next);
    return [next, next === 'dark' ? 'fas fa-sun' : 'fas fa-moon'];
}
"""

#: On first load the store may disagree with what the asset already
#: applied — the store is Dash's copy, the attribute is the browser's.
#: The attribute wins, because it is what the page is actually rendering.
SYNC = """
function (_id, stored) {
    var applied = window.tradingagentsTheme();
    if (stored && stored !== applied) {
        window.tradingagentsSetTheme(stored);
        applied = stored;
    }
    return applied === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
}
"""


def register_theme_callbacks(app):
    # app.clientside_callback, not the module-level function: the global
    # registry the latter writes into is drained when the Dash object is
    # constructed, so anything added afterwards is never attached.
    app.clientside_callback(
        TOGGLE,
        Output("theme-store", "data"),
        Output("theme-toggle-icon", "className"),
        Input("theme-toggle", "n_clicks"),
        State("theme-store", "data"),
        prevent_initial_call=True,
    )

    app.clientside_callback(
        SYNC,
        Output("theme-toggle-icon", "className", allow_duplicate=True),
        Input("theme-toggle", "id"),
        State("theme-store", "data"),
        prevent_initial_call="initial_duplicate",
    )
