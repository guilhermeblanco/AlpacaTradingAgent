"""The shape of the application: five stages, in the order they happen.

The page used to be one scroll of fourteen stacked panels. Everything was
present and nothing was findable, because scroll position is not a
navigation model — you cannot tell someone where a thing is, and you cannot
tell whether you have already passed it.

The stages here are the ones the machine already runs, which is the reason
to prefer them over invented categories like "Dashboard" and "Advanced".
The Decision Tape reports gather → analyze → compute → decide → prepare →
order → react; the navigation groups those seven into the five places an
operator actually stands:

    Watch      start work, and see what is moving
    Decide     the record of one decision, in full
    Evaluate   whether the decisions were any good
    Operate    the account, the brakes, and what things cost
    Set up     what this deployment needs before any of it works

So a panel's home is a question about which stage it belongs to, and that
question has an answer. "Where should the cost panel go" is answerable;
"should the cost panel be above or below the backtest panel" never was.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Stage:
    """One tab."""

    id: str
    label: str
    #: Shown under the tab bar. One line saying what this place is for, so
    #: the answer to "why am I here" does not depend on recognising the
    #: panels.
    blurb: str
    #: Panel factory names, in the order they should appear. Kept as names
    #: rather than imports so this module stays a description of the layout
    #: rather than a second copy of it.
    panels: tuple[str, ...] = field(default_factory=tuple)


STAGES: tuple[Stage, ...] = (
    Stage(
        id="watch",
        label="Watch",
        blurb="Start an analysis, and see everything currently moving through the pipeline.",
        panels=("config_panel", "pipeline_board", "chart_panel", "status_panel"),
    ),
    Stage(
        id="decide",
        label="Decide",
        blurb="One decision end to end: the evidence, the argument, the gates it passed, and what was sent.",
        panels=("workbench", "decision_explorer", "decision_panel", "reports_panel"),
    ),
    Stage(
        id="evaluate",
        label="Evaluate",
        blurb="Whether the decisions were any good — realised outcomes, variant comparisons, backtests.",
        panels=("evaluation_panel", "backtest_panel"),
    ),
    Stage(
        id="operate",
        label="Operate",
        blurb="The account, the brakes, the reconciliation backlog, and what all of this costs.",
        panels=("operations_panel", "safety_panel", "alpaca_account", "cost_panel"),
    ),
    Stage(
        id="setup",
        label="Set up",
        blurb="What this deployment still needs, and every setting behind it.",
        panels=("setup_panel",),
    ),
)

DEFAULT_STAGE = "watch"

#: Where the wizard sends you when setup is incomplete.
SETUP_STAGE = "setup"


def stage(stage_id: str) -> Stage | None:
    return next((item for item in STAGES if item.id == stage_id), None)


def panel_stage(panel: str) -> str:
    """Which stage owns a panel, or "" if nothing does."""
    for item in STAGES:
        if panel in item.panels:
            return item.id
    return ""
