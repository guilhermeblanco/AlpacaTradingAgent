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
PALETTE: dict[str, str] = {
    "background": "#0F172A",
    "surface": "#1E293B",
    "surface-raised": "#243349",
    "surface-sunken": "#0B1220",
    "border": "#334155",
    "border-strong": "#475569",
    "text": "#F1F5F9",
    "text-muted": "#94A3B8",
    "text-faint": "#64748B",
    "accent": "#3B82F6",
    "accent-hover": "#2563EB",
    "positive": "#10B981",
    "caution": "#F59E0B",
    "negative": "#EF4444",
    "neutral": "#94A3B8",
    "info": "#38BDF8",
}

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


def css_variables() -> str:
    """The tokens as custom properties, for the stylesheet to consume."""
    lines = [":root {"]
    for name, value in PALETTE.items():
        lines.append(f"    --ta-{name}: {value};")
    for name, value in SPACING.items():
        lines.append(f"    --ta-space-{name}: {value};")
    for name, value in TYPOGRAPHY.items():
        lines.append(f"    --ta-{name}: {value};")
    for status, (palette_key, _bootstrap) in STATUS_TOKENS.items():
        lines.append(f"    --ta-status-{status}: {PALETTE[palette_key]};")
    lines.append("}")
    return "\n".join(lines)
