"""The shape of the application.

The page was once one scroll of fourteen stacked panels; then five tabs,
one of which was Set up. Set up being a permanent tab is the mistake this
replaces. It is a job that ends, and a tab that never goes away for a job
that ends reads as "still unfinished" forever.

So there is no Set up tab at all. The wizard *is* the setting-up: it
opens by itself while anything required is missing, and Configuration is
where you land when you dismiss it and where the same list lives
afterwards. A tab would have been a third copy of one screen.

    Dashboard      the pipeline, and the analysis running through it
    Decide         one decision end to end
    Evaluate       whether the decisions were any good
    Operate        the account, the brakes, and what it costs
    Configuration  pages of settings, each with sections

What changes with readiness is not which tabs exist but which one you
arrive on: Configuration while something is missing, Dashboard once
nothing is.

The line between a stage and a configuration page is what a person is
doing. A stage answers "what is happening"; a page answers "what should
happen". Every setting that used to live inside a stage has moved to a
page, so a stage is now only ever a view.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Stage:
    """One top-level tab."""

    id: str
    label: str
    #: One line under the tab bar saying what this place is for, so the
    #: answer to "why am I here" does not depend on recognising panels.
    blurb: str
    #: Panel factory names, in the order they should appear. Kept as
    #: names rather than imports so this module stays a description of
    #: the layout rather than a second copy of it.
    panels: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ConfigPage:
    """One page of settings, and the sections it divides into."""

    id: str
    label: str
    blurb: str
    #: (tab label, panel name). Sections are tabs within a page because
    #: "the symbols we track" and "which model runs which phase" are
    #: both Analysis, and neither belongs in a list with the other.
    sections: tuple[tuple[str, str], ...] = field(default_factory=tuple)


STAGES: tuple[Stage, ...] = (
    Stage(
        id="dashboard",
        label="Dashboard",
        blurb="What is moving through the pipeline, and the analysis running now.",
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
        id="configuration",
        label="Configuration",
        blurb="What the machine should do, as against what it is doing.",
        panels=("configuration",),
    ),
)

CONFIG_PAGES: tuple[ConfigPage, ...] = (
    ConfigPage(
        id="platform",
        label="Platform",
        blurb="What it may do, what it may spend, and how hard it may push.",
        sections=(("Settings", "platform_settings"),),
    ),
    ConfigPage(
        id="requirements",
        label="Requirements",
        blurb=(
            "Every third party this configuration can use, whether it is set, "
            "and where the value comes from."
        ),
        sections=(("Requirements", "setup_panel"),),
    ),
)

DEFAULT_STAGE = "dashboard"

CONFIG_STAGE = "configuration"
DEFAULT_CONFIG_PAGE = "platform"

#: Where the wizard sends you, and where dismissing it leaves you.
SETUP_STAGE = CONFIG_STAGE


def landing_stage(ready: bool) -> str:
    """Which tab to arrive on.

    Dashboard is the answer once a deployment works. Before that it is a
    board with nothing on it, so Configuration — where the wizard sits
    and where the list of what is missing lives — is the more useful
    place to be standing when the wizard is dismissed.
    """
    return DEFAULT_STAGE if ready else CONFIG_STAGE


def stage(stage_id: str) -> Stage | None:
    return next((item for item in STAGES if item.id == stage_id), None)


def config_page(page_id: str) -> ConfigPage | None:
    return next((item for item in CONFIG_PAGES if item.id == page_id), None)


def panel_stage(panel: str) -> str:
    """Which stage owns a panel, or "" if nothing does."""
    for item in STAGES:
        if panel in item.panels:
            return item.id
    for page in CONFIG_PAGES:
        if any(panel == name for _label, name in page.sections):
            return CONFIG_STAGE
    return ""
