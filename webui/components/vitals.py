"""The machine's vital signs, pinned above everything else.

Whether the machine is running, what it will do next, what is holding it
back, and how much budget is left. All of it already existed — spread
across the ops cockpit, the safety panel, and a status line under the
config form — which meant an operator had to go looking for the answer to
"is this thing working right now?".
"""

from dash import dcc, html

from webui.config.tokens import status_color


#: "ok", "busy", "bad", and "idle" are aliases in the shared vocabulary, so
#: a healthy worker here is the same green as a completed stage on the tape.
def vital(label, value, tone="idle", hint=""):
    """One reading: a label, a value, and a colour that means something."""
    return html.Div(
        [
            html.Div(label, className="vitals-label"),
            html.Div(
                value,
                className="vitals-value",
                style={"color": status_color(tone)},
            ),
            html.Div(hint, className="vitals-hint") if hint else None,
        ],
        className="vitals-cell",
        title=hint or None,
    )


def create_vitals_strip():
    return html.Section(
        [
            dcc.Interval(id="vitals-interval", interval=10_000),
            html.Div(id="vitals-strip", className="vitals-strip"),
        ],
        className="mb-3",
    )
