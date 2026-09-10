"""Design tokens: one source for colour, spacing, and status vocabulary.

Before this, the same palette existed three times — a `COLORS` dict read by
Python, hex literals in the stylesheet, and one-off hex strings inline in
components — so a colour change had to be made in three places and usually
was not. Status vocabulary was worse: "completed" was green in the status
table, "done" green in the tape, "passed" green in the gate ledger, and
each panel decided for itself what badge to use.

Everything here is defined once and exported two ways: as Python values
for code that builds Plotly figures and inline styles, and as CSS custom
properties for the stylesheet. `PALETTE` is the authority; `COLORS` in
`webui.config.constants` is a view onto it.
"""

from __future__ import annotations

#: Surfaces, text, and lines. Named by role, not by hue, so a theme change
#: is a change of value rather than a rename.
#: Every colour the interface uses, named for what it is *for* rather than
#: what it looks like — which is the only way a second theme is possible.
#:
#: The stylesheet used to carry 47 distinct hex values across two colour
#: families (Tailwind slate and Tailwind gray) doing the same jobs, plus
#: 131 `rgba()` literals that were alpha variants of those same colours.
#: They are all one of the names below now.
DARK_PALETTE: dict[str, str] = {
    # Ground and surfaces, from furthest back to nearest front.
    "background": "#0F172A",
    "surface-sunken": "#0B1220",
    "surface": "#1E293B",
    "surface-inset": "#172033",
    "surface-raised": "#243349",
    # Lines.
    "border": "#334155",
    "border-strong": "#475569",
    # Type, brightest to faintest.
    "text-bright": "#F8FAFC",
    "text": "#F1F5F9",
    "text-soft": "#CBD5E1",
    "text-muted": "#94A3B8",
    "text-faint": "#64748B",
    # Accent, and the tints and shades built on it.
    "accent": "#3B82F6",
    "accent-hover": "#2563EB",
    "accent-strong": "#1D4ED8",
    "accent-deep": "#1E40AF",
    "accent-light": "#60A5FA",
    "accent-lighter": "#93C5FD",
    # Outcomes.
    "positive": "#10B981",
    "positive-strong": "#059669",
    "positive-deep": "#047857",
    "positive-deeper": "#065F46",
    "positive-light": "#34D399",
    "positive-lighter": "#6EE7B7",
    "caution": "#F59E0B",
    "caution-light": "#FCD34D",
    "negative": "#EF4444",
    "negative-light": "#FCA5A5",
    "neutral": "#94A3B8",
    "info": "#38BDF8",
    "info-light": "#7DD3FC",
    "highlight": "#2DD4BF",
    "highlight-light": "#5EEAD4",
    # What a shadow is cast in. A theme decides this too: on a light
    # ground a black shadow is right, on a dark one it is barely visible
    # and the border does the work instead.
    "shadow": "#000000",
}

#: The one the interface renders in today. A light palette lands beside
#: this in the next step; keeping the dark values byte-identical here is
#: what makes that step reviewable.
PALETTE: dict[str, str] = dict(DARK_PALETTE)


def rgb_triplet(value: str) -> str:
    """`#3B82F6` → `59 130 246`, for `rgb(var(--x) / 0.1)`.

    The stylesheet needs alpha variants of palette colours — a hover
    wash, a focus ring, a shadow. Writing those as `rgba(59, 130, 246,
    0.1)` pins the colour to one theme, so each token also publishes its
    channels and the alpha is applied at the point of use.
    """
    raw = value.lstrip("#")
    return " ".join(str(int(raw[index : index + 2], 16)) for index in (0, 2, 4))

#: Spacing scale, in pixels. Four steps is enough for a dense workbench and
#: few enough that layouts stay on the grid.
SPACING: dict[str, str] = {
    "xs": "4px",
    "sm": "8px",
    "md": "14px",
    "lg": "22px",
}

TYPOGRAPHY: dict[str, str] = {
    "size-micro": "10px",
    "size-small": "11px",
    "size-body": "13px",
    "size-heading": "17px",
    "weight-normal": "400",
    "weight-medium": "600",
    "radius": "6px",
    "radius-lg": "8px",
}


class Status:
    """The states anything in the pipeline can be in.

    One vocabulary for agents, stages, gates, and workers, so the same
    condition looks the same everywhere it is shown.
    """

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"
    CLIPPED = "clipped"


#: (palette key, Bootstrap colour) per status.
STATUS_TOKENS: dict[str, tuple[str, str]] = {
    Status.PENDING: ("neutral", "secondary"),
    Status.RUNNING: ("caution", "warning"),
    Status.DONE: ("positive", "success"),
    Status.BLOCKED: ("negative", "danger"),
    Status.FAILED: ("negative", "danger"),
    Status.SKIPPED: ("text-faint", "secondary"),
    Status.CLIPPED: ("caution", "warning"),
}

#: Words other layers use for the same conditions, mapped onto the vocabulary.
STATUS_ALIASES: dict[str, str] = {
    "completed": Status.DONE,
    "in_progress": Status.RUNNING,
    "passed": Status.DONE,
    "error": Status.FAILED,
    "healthy": Status.DONE,
    "degraded": Status.BLOCKED,
    "stale": Status.FAILED,
    "paused": Status.BLOCKED,
    "ok": Status.DONE,
    "busy": Status.RUNNING,
    "bad": Status.FAILED,
    "idle": Status.PENDING,
}


def normalize_status(value: str) -> str:
    """Map any of the layers' words onto the shared vocabulary."""
    key = str(value or "").strip().lower()
    return STATUS_ALIASES.get(key, key if key in STATUS_TOKENS else Status.PENDING)


def status_color(value: str) -> str:
    """The hex a status is drawn in, wherever it is drawn."""
    palette_key, _bootstrap = STATUS_TOKENS[normalize_status(value)]
    return PALETTE[palette_key]


def status_badge(value: str) -> str:
    """The Bootstrap colour a status badge uses, wherever it is shown."""
    _palette_key, bootstrap = STATUS_TOKENS[normalize_status(value)]
    return bootstrap


def palette_variables(palette: dict[str, str], selector: str = ":root") -> str:
    """One palette as custom properties, under one selector."""
    lines = [f"{selector} {{"]
    for name, value in palette.items():
        lines.append(f"    --ta-{name}: {value};")
        lines.append(f"    --ta-{name}-rgb: {rgb_triplet(value)};")
    for status, (palette_key, _bootstrap) in STATUS_TOKENS.items():
        lines.append(f"    --ta-status-{status}: {palette[palette_key]};")
    lines.append("}")
    return "\n".join(lines)


def css_variables() -> str:
    """The tokens as custom properties, for the stylesheet to consume."""
    lines = [palette_variables(PALETTE)]
    scale = [":root {"]
    for name, value in SPACING.items():
        scale.append(f"    --ta-space-{name}: {value};")
    for name, value in TYPOGRAPHY.items():
        scale.append(f"    --ta-{name}: {value};")
    scale.append("}")
    lines.append("\n".join(scale))
    return "\n\n".join(lines)
