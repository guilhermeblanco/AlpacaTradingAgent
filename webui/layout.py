"""Assembling the application.

The layout used to be one scroll of fourteen stacked panels. This puts them
into the five stages described in webui/config/navigation.py, with the
vitals strip pinned above the tab bar because "is this working right now?"
is a question you should never have to navigate to answer.

Two properties are load-bearing, and both are tested:

*Every panel stays in the tree.* Bootstrap tab panes are hidden with CSS,
not unmounted, so a `dcc.Interval` on the Operate tab keeps ticking while
you are reading the Decide tab, and a callback whose Output lives on
another tab still resolves. Rendering tab bodies lazily would have been
tidier and would have broken roughly ninety callbacks.

*Panels cannot outgrow their column.* Each one sits in a shell that sets
`min-width: 0` — a flex or grid child defaults to `min-width: auto`, which
means "as wide as my widest child", which is how one long table used to
push the whole page sideways. Wide content scrolls inside its own box.
"""

from dash import dcc, html
import dash_bootstrap_components as dbc

from webui.components.header import create_header
from webui.components.config_panel import create_config_panel
from webui.components.status_panel import create_status_panel
from webui.components.chart_panel import create_chart_panel
from webui.components.decision_panel import create_decision_panel
from webui.components.reports_panel import create_reports_panel
from webui.components.backtest_panel import create_backtest_panel
from webui.components.alpaca_account import render_alpaca_account_section
from webui.components.safety_panel import create_safety_panel
from webui.components.cost_panel import create_cost_panel
from webui.components.api_config_modal import create_api_config_modal
from webui.components.configuration import create_configuration
from webui.components.integrations_panel import (
    create_integrations_panel,
    integration_editor_modal,
)
from webui.components.operations_panel import create_operations_panel
from webui.components.decision_explorer import create_decision_explorer
from webui.components.workbench import create_workbench_panel
from webui.components.pipeline_board import create_pipeline_board
from webui.components.vitals import create_vitals_strip
from webui.callbacks.pulse_callbacks import create_pulse_components
from webui.components.evaluation_panel import create_evaluation_panel
from webui.components.platform_settings import create_platform_settings
from webui.components.setup_panel import create_setup_panel
from webui.components.setup_wizard import create_setup_wizard
from webui.config.constants import COLORS, REFRESH_INTERVALS
from webui.config.navigation import STAGES, landing_stage


def create_intervals():
    """Create interval components for auto-refresh"""
    return [
        # Nudged by the server-sent pulse the moment state advances; the
        # interval itself is the fallback for a proxy that eats the stream.
        dcc.Interval(
            id='refresh-interval',
            interval=REFRESH_INTERVALS["fast"],
            n_intervals=0,
        ),

        # Reports and other non-critical panels.
        dcc.Interval(
            id='medium-refresh-interval',
            interval=REFRESH_INTERVALS["medium"],
            n_intervals=0,
        ),

        # Slow refresh for account data
        dcc.Interval(
            id='slow-refresh-interval',
            interval=REFRESH_INTERVALS["slow"],
            n_intervals=0,
            disabled=False  # Always enabled for account data
        )
    ]


def create_stores():
    """Create store components for state management"""
    from webui.utils.storage import create_storage_store_component, create_api_keys_store_component
    return [
        dcc.Store(id='app-store'),
        dcc.Store(id='chart-store', data={'last_symbol': None, 'selected_period': '1y'}),
        create_storage_store_component(),
        create_api_keys_store_component()
    ]


def _alpaca_account_card():
    return dbc.Card(
        dbc.CardBody([render_alpaca_account_section()]),
        className="mb-4",
    )


#: Panel name → the factory that builds it. The names come from
#: webui/config/navigation.py, which describes the arrangement; this maps
#: them onto the components without the description having to import any.
PANEL_FACTORIES = {
    "config_panel": create_config_panel,
    "pipeline_board": create_pipeline_board,
    "chart_panel": create_chart_panel,
    "status_panel": create_status_panel,
    "workbench": create_workbench_panel,
    "decision_explorer": create_decision_explorer,
    "decision_panel": create_decision_panel,
    "reports_panel": create_reports_panel,
    "evaluation_panel": create_evaluation_panel,
    "backtest_panel": create_backtest_panel,
    "operations_panel": create_operations_panel,
    "safety_panel": create_safety_panel,
    "alpaca_account": _alpaca_account_card,
    "cost_panel": create_cost_panel,
    # Reached through Configuration → Requirements rather than a stage
    # of its own; see webui/config/navigation.py.
    "setup_panel": create_setup_panel,
    "platform_settings": create_platform_settings,
    "integrations_panel": create_integrations_panel,
}

# The Configuration stage is built from the table above rather than from
# imports of its own, so adding a panel to a page does not mean listing
# it in two places.
PANEL_FACTORIES["configuration"] = lambda: create_configuration(PANEL_FACTORIES)


def panel_shell(name, component):
    """One panel, boxed so it cannot push the page sideways.

    `min-width: 0` is the whole trick. A flex or grid child defaults to
    `min-width: auto`, meaning "at least as wide as my widest content", so
    a single long table or a wide Plotly figure widens its column, then its
    row, then the page. Setting it to zero lets the column win and the
    content scroll inside `.panel-shell-body` instead.
    """
    return html.Section(
        html.Div(component, className="panel-shell-body"),
        id=f"panel-{name.replace('_', '-')}",
        className="panel-shell",
    )


def create_stage_body(stage):
    """Everything that belongs to one tab."""
    return html.Div(
        [
            html.P(stage.blurb, className="stage-blurb"),
            *(
                panel_shell(name, PANEL_FACTORIES[name]())
                for name in stage.panels
                if name in PANEL_FACTORIES
            ),
        ],
        className="stage-body",
    )


def setup_is_complete() -> bool:
    """Whether everything required is configured.

    Read at layout time, and only to decide which tab you arrive on. It
    re-evaluates on a reload, which is the right granularity for
    something that changes about once.
    """
    try:
        from tradingagents.setup import evaluate_readiness

        return evaluate_readiness().ready
    except Exception:
        # Unable to tell. Landing on Configuration is the recoverable
        # mistake; landing on an empty Dashboard when configuration is
        # what is broken is not.
        return False


def create_stage_tabs(ready: bool | None = None):
    """The tab bar and every stage's content.

    All bodies are built here rather than in a callback. Bootstrap hides
    an inactive pane with CSS and leaves it mounted, so intervals keep
    running and callbacks keep resolving across tabs; building lazily
    would break both for the sake of a first paint nobody is waiting on.
    """
    ready = setup_is_complete() if ready is None else ready
    return dbc.Tabs(
        [
            dbc.Tab(
                create_stage_body(stage),
                label=stage.label,
                tab_id=stage.id,
                tab_class_name="stage-tab",
                active_tab_class_name="stage-tab-active",
            )
            for stage in STAGES
        ],
        id="stage-tabs",
        # Dashboard once the deployment works; before that it is a board
        # with nothing on it, so Configuration is the more useful place
        # to be standing when the wizard is dismissed.
        active_tab=landing_stage(ready),
        className="stage-tabs",
    )


def create_footer():
    """Create the footer section"""
    return html.Div(
        [
            dbc.Button(
                "Refresh now",
                id="refresh-btn",
                color="secondary",
                size="sm",
                className="me-2",
            ),
            html.Span(
                "Panels update themselves as state changes.",
                className="text-muted small",
            ),
        ],
        className="app-footer d-flex align-items-center justify-content-center",
    )


def create_main_layout():
    """Create the main application layout"""
    return dbc.Container(
        [
            *create_intervals(),
            *create_stores(),
            create_api_config_modal(),
            # Mounted globally rather than inside the page, so the
            # editor is reachable from wherever an integration is listed.
            integration_editor_modal(),
            create_setup_wizard(),
            *create_pulse_components(),

            create_header(),

            # Above the tabs on purpose: "is this working right now?" should
            # never be a question you have to navigate to answer.
            create_vitals_strip(),

            create_stage_tabs(),

            html.Div(className="mt-4"),
            create_footer(),
        ],
        fluid=True,
        className="p-4 app-shell",
        style={"backgroundColor": COLORS["background"]},
    )
