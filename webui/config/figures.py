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

from webui.config.tokens import DEFAULT_THEME, normalize_theme, palette_for

#: Comfortable rather than tight. `t` leaves room for a title, and the
#: others are a floor — `automargin` grows them when labels need it.
MARGIN = {"l": 12, "r": 16, "t": 40, "b": 12}


def base_layout(theme: str = DEFAULT_THEME) -> dict:
    """The shared layout, in one theme's colours.

    A figure is rendered in Python and shipped as JSON, so unlike every
    other surface it cannot pick up a CSS variable — it has to be told
    which palette to draw in. That is why every callback that builds one
    takes the theme store as an Input.
    """
    palette = palette_for(theme)
    return {
        "template": "plotly_dark" if normalize_theme(theme) == "dark" else "plotly_white",
        "paper_bgcolor": palette["surface"],
        "plot_bgcolor": palette["surface"],
        "margin": MARGIN,
        "font": {"color": palette["text"], "size": 12},
        "showlegend": False,
        "hoverlabel": {"font": {"size": 12}},
        "title": {
            "font": {"size": 13, "color": palette["text"]},
            "x": 0,
            "xanchor": "left",
            "y": 0.97,
            "yanchor": "top",
        },
        # Bar labels shrink to fit by default, which on a small chart
        # means they shrink to unreadable. Below the floor they are
        # hidden instead — a label you cannot read is not a label.
        "uniformtext": {"mode": "hide", "minsize": 10},
    }

#: Taller than before. A waterfall with four labelled steps in 260 pixels
#: is a diagram of a waterfall rather than a reading of one.
HEIGHT_SMALL = 220
HEIGHT_MEDIUM = 300
HEIGHT_LARGE = 380


def translucent(hex_value: str, alpha: float) -> str:
    """A Plotly colour with alpha, from a palette value.

    Plotly is handed JSON, not CSS, so `rgb(var(--x) / 0.1)` means
    nothing to it — the variable is never resolved and the colour is
    silently dropped. Figures take the palette's value and build the
    rgba themselves.
    """
    from webui.config.tokens import rgb_triplet

    return f"rgba({rgb_triplet(hex_value).replace(' ', ', ')}, {alpha})"


def style(
    figure: go.Figure,
    *,
    title: str = "",
    height: int = HEIGHT_MEDIUM,
    theme: str = DEFAULT_THEME,
) -> go.Figure:
    """Apply the shared layout, then let the axes claim what they need."""
    palette = palette_for(theme)
    layout = base_layout(theme)
    if title:
        layout["title"] = {**layout["title"], "text": title}
    figure.update_layout(**layout, height=height)
    figure.update_xaxes(automargin=True, gridcolor=palette["border"])
    figure.update_yaxes(automargin=True, gridcolor=palette["border"])
    return figure


def empty_figure(
    message: str, *, height: int = HEIGHT_SMALL, theme: str = DEFAULT_THEME
) -> go.Figure:
    """A chart with nothing in it, saying why rather than showing an axis.

    Worth more care than it looks: on a fresh deployment every chart is
    this one, so it is the first impression of the whole workbench.
    """
    palette = palette_for(theme)
    figure = go.Figure()
    figure.update_layout(
        **{**base_layout(theme), "margin": {"l": 8, "r": 8, "t": 8, "b": 8}},
        height=height,
        xaxis={"visible": False},
        yaxis={"visible": False},
        annotations=[
            {
                "text": message,
                "showarrow": False,
                "font": {"color": palette["text-faint"], "size": 12},
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
