"""One way to lay out a figure.

The previous shared layout was a dict living in the workbench panel, which
the pipeline board imported from it — so the board depended on the
workbench in order to draw a bar chart. It also set
`margin: {l: 8, r: 8, t: 28, b: 8}`, which is the reason the charts were
hard to read: eight pixels is not enough room for a tick label, so Plotly
drew the axis and clipped the numbers off the side of it.

The fix is mostly `automargin`. It lets each axis claim the space its own
labels need instead of every chart guessing a margin that has to work for
all of them, and the guess being wrong in the direction of illegible.

Sizes are up too. Eleven-pixel text on a dark background inside a 180-pixel
figure was small before anyone put it in a column half the page wide.
"""

from __future__ import annotations

import plotly.graph_objects as go

from webui.config.constants import COLORS

#: Comfortable rather than tight. `t` leaves room for a title, and the
#: others are a floor — `automargin` grows them when labels need it.
MARGIN = {"l": 12, "r": 16, "t": 40, "b": 12}

BASE_LAYOUT = {
    "template": "plotly_dark",
    "paper_bgcolor": COLORS["card"],
    "plot_bgcolor": COLORS["card"],
    "margin": MARGIN,
    "font": {"color": COLORS["text"], "size": 12},
    "showlegend": False,
    "hoverlabel": {"font": {"size": 12}},
    "title": {
        "font": {"size": 13, "color": COLORS["text"]},
        "x": 0,
        "xanchor": "left",
        "y": 0.97,
        "yanchor": "top",
    },
    # Bar labels shrink to fit by default, which on a small chart means
    # they shrink to unreadable. Below the floor they are hidden instead,
    # which is the honest outcome — a label you cannot read is not a label.
    "uniformtext": {"mode": "hide", "minsize": 10},
}

#: Taller than before. A waterfall with four labelled steps in 260 pixels
#: is a diagram of a waterfall rather than a reading of one.
HEIGHT_SMALL = 220
HEIGHT_MEDIUM = 300
HEIGHT_LARGE = 380


def style(figure: go.Figure, *, title: str = "", height: int = HEIGHT_MEDIUM) -> go.Figure:
    """Apply the shared layout, then let the axes claim what they need."""
    layout = dict(BASE_LAYOUT)
    if title:
        layout["title"] = {**BASE_LAYOUT["title"], "text": title}
    figure.update_layout(**layout, height=height)
    figure.update_xaxes(automargin=True, gridcolor=COLORS["border"])
    figure.update_yaxes(automargin=True, gridcolor=COLORS["border"])
    return figure


def empty_figure(message: str, *, height: int = HEIGHT_SMALL) -> go.Figure:
    """A chart with nothing in it, saying why rather than showing an axis.

    Worth more care than it looks: on a fresh deployment every chart is
    this one, so it is the first impression of the whole workbench.
    """
    figure = go.Figure()
    figure.update_layout(
        **{**BASE_LAYOUT, "margin": {"l": 8, "r": 8, "t": 8, "b": 8}},
        height=height,
        xaxis={"visible": False},
        yaxis={"visible": False},
        annotations=[
            {
                "text": message,
                "showarrow": False,
                "font": {"color": COLORS["pending"], "size": 12},
                "xref": "paper",
                "yref": "paper",
                "x": 0.5,
                "y": 0.5,
                # Wraps rather than running off the edge of a narrow column.
                "align": "center",
            }
        ],
    )
    return figure
