"""Tests for the stage navigation and the things a tabbed layout breaks.

Splitting one long scroll into five tabs is easy. The two ways it goes
wrong are less obvious, and both are tested here:

*Panels quietly disappearing.* A panel is now in exactly one stage. Anything
present in the panel table and absent from the navigation is unreachable —
built, registered, callbacks and all, and on no screen.

*Measuring while hidden.* A Bootstrap tab pane is hidden with CSS rather
than unmounted, which is what keeps intervals and cross-tab callbacks
working. The cost is that a figure drawn while hidden measures zero, so
every figure has to be responsive.
"""

from __future__ import annotations

import re
import pathlib
import unittest

from webui.config.navigation import DEFAULT_STAGE, STAGES, panel_stage, stage
from webui.layout import PANEL_FACTORIES

COMPONENTS = pathlib.Path(__file__).resolve().parent.parent / "webui" / "components"


class StageTests(unittest.TestCase):
    def test_the_stages_are_the_lifecycle_then_the_settings(self):
        """Set up is not among them: it is a job that ends, and a tab
        that never goes away for a job that ends reads as unfinished
        forever. The wizard is the setting-up, and Configuration holds
        the same list afterwards."""
        self.assertEqual(
            [item.id for item in STAGES],
            ["dashboard", "decide", "evaluate", "operate", "configuration"],
        )

    def test_the_default_stage_exists(self):
        self.assertIsNotNone(stage(DEFAULT_STAGE))

    def test_every_stage_says_what_it_is_for(self):
        """A tab label is a noun; the blurb is why you would go there."""
        for item in STAGES:
            with self.subTest(stage=item.id):
                self.assertTrue(item.blurb.strip())
                self.assertTrue(item.blurb.endswith("."))

    def test_every_stage_has_at_least_one_panel(self):
        for item in STAGES:
            with self.subTest(stage=item.id):
                self.assertTrue(item.panels)


class PanelPlacementTests(unittest.TestCase):
    """The failure mode is a panel that exists and is on no screen."""

    def test_every_panel_in_the_navigation_can_be_built(self):
        for item in STAGES:
            for panel in item.panels:
                with self.subTest(stage=item.id, panel=panel):
                    self.assertIn(panel, PANEL_FACTORIES)

    def test_every_buildable_panel_is_placed_somewhere(self):
        for panel in PANEL_FACTORIES:
            with self.subTest(panel=panel):
                self.assertTrue(
                    panel_stage(panel),
                    f"{panel} can be built but no stage shows it",
                )

    def test_no_panel_is_in_two_places(self):
        seen: dict[str, str] = {}
        for item in STAGES:
            for panel in item.panels:
                with self.subTest(panel=panel):
                    self.assertNotIn(
                        panel, seen, f"{panel} is in {seen.get(panel)} and {item.id}"
                    )
                    seen[panel] = item.id

    def test_the_analysis_settings_are_a_configuration_page(self):
        """The Dashboard shows an analysis happening; what an analysis
        *is* — which symbols, which models, how deeply — is a setting,
        and it was sitting on the Dashboard under its own heading."""
        self.assertEqual(panel_stage("config_panel"), "configuration")

    def test_the_dashboard_is_only_what_is_happening(self):
        from webui.config.navigation import stage

        self.assertEqual(
            stage("dashboard").panels,
            ("pipeline_board", "chart_panel", "status_panel"),
        )

    def test_the_requirements_list_lives_under_configuration(self):
        self.assertEqual(panel_stage("setup_panel"), "configuration")

    def test_you_land_where_there_is_something_to_do(self):
        """An empty Dashboard tells a half-configured deployment
        nothing; the list of what is missing tells it everything."""
        from webui.config.navigation import landing_stage

        self.assertEqual(landing_stage(True), "dashboard")
        self.assertEqual(landing_stage(False), "configuration")

    def test_no_panel_is_in_a_stage_and_a_configuration_page_at_once(self):
        """Rendering one twice is two components answering to one id."""
        from webui.config.navigation import CONFIG_PAGES

        in_pages = {name for page in CONFIG_PAGES for _label, name in page.sections}
        in_stages = {panel for item in STAGES for panel in item.panels}

        self.assertEqual(in_pages & in_stages, set())


class MountedContentTests(unittest.TestCase):
    """Every tab's content is in the tree, not built on demand."""

    def _layout(self):
        from webui.layout import create_main_layout

        return create_main_layout()

    def _ids(self, component, found=None):
        found = set() if found is None else found
        component_id = getattr(component, "id", None)
        if isinstance(component_id, str):
            found.add(component_id)
        children = getattr(component, "children", None)
        if isinstance(children, (list, tuple)):
            for child in children:
                self._ids(child, found)
        elif children is not None:
            self._ids(children, found)
        return found

    def test_a_component_from_every_stage_is_present_at_once(self):
        """Lazy tab bodies would have been tidier and would have broken
        roughly ninety callbacks whose Output lives on another tab."""
        ids = self._ids(self._layout())

        for expected in (
            "panel-pipeline-board",
            "panel-workbench",
            "panel-evaluation-panel",
            "panel-safety-panel",
            "panel-configuration",
        ):
            with self.subTest(panel=expected):
                self.assertIn(expected, ids)

    def test_the_analysis_settings_are_mounted_too(self):
        """They moved to a Configuration page, which is hidden with CSS
        rather than built on demand — so every callback wired to the run
        form still resolves."""
        self.assertIn("ticker-input", self._ids(self._layout()))

    def test_the_vitals_strip_is_outside_the_tabs(self):
        """"Is this working right now?" should not require navigating."""
        from webui.layout import create_stage_tabs

        self.assertNotIn("vitals-strip", self._ids(create_stage_tabs()))
        self.assertIn("vitals-strip", self._ids(self._layout()))


class FigureSizingTests(unittest.TestCase):
    def test_every_graph_is_responsive(self):
        """A figure drawn inside a hidden pane measures zero and keeps that
        width; `responsive` is what makes it re-measure on the resize the
        tab switch fires."""
        pattern = re.compile(r"dcc\.Graph\((.*?)\n\s*\)", re.S)

        for path in sorted(COMPONENTS.glob("*.py")):
            source = path.read_text()
            for match in pattern.finditer(source):
                block = match.group(1)
                identifier = re.search(r'id=[\'"]([\w-]+)', block)
                with self.subTest(file=path.name, graph=identifier and identifier.group(1)):
                    self.assertIn(
                        "responsive", block, f"{path.name}: graph is not responsive"
                    )


class ServedStylesheetTests(unittest.TestCase):
    """The stylesheet reaches a browser.

    webui/utils/styles.py held the design tokens and every workbench rule,
    and nothing imported it — so the board cards and the vitals strip had
    been rendering unstyled while a test asserting the *contents* of that
    string passed happily. Asserting what is served rather than what is
    generated is the only version of this test worth having.
    """

    def _index(self):
        from webui.app_dash import create_app

        return create_app().index_string

    def test_the_design_tokens_are_served(self):
        self.assertIn("--ta-accent", self._index())

    def test_the_workbench_rules_are_served(self):
        index = self._index()

        for rule in (".board-card", ".vitals-strip", ".panel-shell", ".stage-tabs"):
            with self.subTest(rule=rule):
                self.assertIn(rule, index)

    def test_the_index_still_has_the_placeholders_dash_needs(self):
        index = self._index()

        for placeholder in ("{%app_entry%}", "{%config%}", "{%scripts%}", "{%renderer%}"):
            with self.subTest(placeholder=placeholder):
                self.assertIn(placeholder, index)

    def test_no_rule_uses_a_token_that_is_not_defined(self):
        from webui.config.tokens import css_variables
        from webui.utils.styles import CSS

        defined = set(re.findall(r"(--ta-[a-z0-9-]+)\s*:", css_variables()))
        used = set(re.findall(r"var\((--ta-[a-z0-9-]+)\)", CSS))

        self.assertEqual(used - defined, set())



class FigureStyleTests(unittest.TestCase):
    """Charts you can actually read.

    The shared layout used to set `margin: {l: 8, r: 8, t: 28, b: 8}`, and
    eight pixels is not enough room for a tick label — Plotly drew the axis
    and clipped the numbers off the side. `automargin` is most of the fix:
    each axis claims what its own labels need, instead of every chart
    sharing one guess that has to work for all of them.
    """

    FIGURES = (
        ("webui.components.workbench", "gate_waterfall_figure",
         [{"label": "Requested", "kind": "start", "amount": 1000, "running": 1000},
          {"label": "Sent", "kind": "total", "amount": 800, "running": 800}]),
        ("webui.components.workbench", "evidence_figure",
         [{"label": "Bullish", "value": 0.6}]),
        ("webui.components.workbench", "outcome_figure",
         [{"horizon": "1d", "excess_return_pct": 1.2}]),
        ("webui.components.pipeline_board", "stage_distribution_figure",
         {"decide": 2, "order": 1}),
        ("webui.components.pipeline_board", "halt_breakdown_figure", {"risk sizing": 2}),
        ("webui.components.pipeline_board", "throughput_figure",
         [{"at": "10:00", "count": 3}]),
    )

    def _figures(self):
        import importlib

        for module_name, function_name, argument in self.FIGURES:
            module = importlib.import_module(module_name)
            yield function_name, getattr(module, function_name)(argument)

    def test_both_axes_claim_the_room_their_labels_need(self):
        for name, figure in self._figures():
            with self.subTest(figure=name):
                self.assertTrue(figure.layout.xaxis.automargin, name)
                self.assertTrue(figure.layout.yaxis.automargin, name)

    def test_no_margin_is_too_small_for_a_tick_label(self):
        for name, figure in self._figures():
            margin = figure.layout.margin
            with self.subTest(figure=name):
                self.assertGreaterEqual(min(margin.l, margin.r, margin.b), 12, name)

    def test_every_chart_is_tall_enough_to_read(self):
        for name, figure in self._figures():
            with self.subTest(figure=name):
                self.assertGreaterEqual(figure.layout.height, 200, name)

    def test_a_label_too_small_to_read_is_hidden_rather_than_shrunk(self):
        for name, figure in self._figures():
            with self.subTest(figure=name):
                self.assertEqual(figure.layout.uniformtext.mode, "hide")
                self.assertGreaterEqual(figure.layout.uniformtext.minsize, 10)

    def test_the_board_no_longer_imports_its_chart_style_from_the_workbench(self):
        """A bar chart on the board should not depend on a different
        panel in order to know what a chart looks like."""
        source = (
            pathlib.Path(__file__).resolve().parent.parent
            / "webui" / "components" / "pipeline_board.py"
        ).read_text()

        self.assertNotIn("from webui.components.workbench import CHART_LAYOUT", source)
        self.assertIn("from webui.config.figures import", source)

    def test_an_empty_chart_says_why_in_the_middle_of_itself(self):
        from webui.config.figures import empty_figure

        figure = empty_figure("Nothing recorded yet")
        annotation = figure.layout.annotations[0]

        self.assertEqual(annotation.text, "Nothing recorded yet")
        self.assertEqual((annotation.x, annotation.y), (0.5, 0.5))
        self.assertFalse(figure.layout.xaxis.visible)



class ConfigurationTests(unittest.TestCase):
    """Pages of settings, each divided into sections."""

    def test_every_page_says_what_it_is_for(self):
        from webui.config.navigation import CONFIG_PAGES

        for page in CONFIG_PAGES:
            with self.subTest(page=page.id):
                self.assertTrue(page.blurb.strip())
                self.assertTrue(page.sections)

    def test_every_section_names_a_panel_that_can_be_built(self):
        from webui.config.navigation import CONFIG_PAGES

        for page in CONFIG_PAGES:
            for _label, name in page.sections:
                with self.subTest(page=page.id, panel=name):
                    self.assertIn(name, PANEL_FACTORIES)

    def test_one_page_is_visible_and_the_rest_are_not(self):
        from webui.components.configuration import create_configuration
        from webui.config.navigation import CONFIG_PAGES, DEFAULT_CONFIG_PAGE

        rendered = create_configuration(PANEL_FACTORIES)
        pages = _find(
            rendered,
            lambda node: isinstance(getattr(node, "id", None), dict)
            and node.id.get("type") == "config-page",
        )

        self.assertEqual(len(pages), len(CONFIG_PAGES))
        visible = [page for page in pages if (page.style or {}).get("display") != "none"]
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0].id["page"], DEFAULT_CONFIG_PAGE)

    def test_every_page_is_mounted_even_while_hidden(self):
        """Hidden with CSS rather than built on demand: a callback whose
        Output is on another page has to keep resolving, and the
        intervals refreshing these panels have to keep ticking."""
        from webui.components.configuration import create_configuration

        rendered = create_configuration(PANEL_FACTORIES)
        found = _ids_of(rendered)

        self.assertIn("setup-readiness", found)
        self.assertIn("platform-settings-body", found)


def _find(component, predicate, found=None):
    found = [] if found is None else found
    if predicate(component):
        found.append(component)
    children = getattr(component, "children", None)
    if isinstance(children, (list, tuple)):
        for child in children:
            _find(child, predicate, found)
    elif children is not None:
        _find(children, predicate, found)
    return found


def _ids_of(component, found=None):
    found = set() if found is None else found
    identifier = getattr(component, "id", None)
    if isinstance(identifier, str):
        found.add(identifier)
    children = getattr(component, "children", None)
    if isinstance(children, (list, tuple)):
        for child in children:
            _ids_of(child, found)
    elif children is not None:
        _ids_of(children, found)
    return found



class UniqueIdTests(unittest.TestCase):
    def test_no_two_components_answer_to_one_id(self):
        """Everything is mounted at once — stage panes, configuration
        pages, four modals — so a panel placed in two homes is two
        components with one id, and Dash resolves that to whichever it
        happened to find. It is silent, and it is the failure this whole
        arrangement invites."""
        from collections import Counter

        from webui.layout import create_main_layout

        found: list[str] = []

        def walk(component):
            identifier = getattr(component, "id", None)
            if isinstance(identifier, str):
                found.append(identifier)
            elif isinstance(identifier, dict):
                found.append(repr(sorted(identifier.items())))
            children = getattr(component, "children", None)
            if isinstance(children, (list, tuple)):
                for child in children:
                    walk(child)
            elif children is not None:
                walk(children)

        walk(create_main_layout())
        duplicates = {key: count for key, count in Counter(found).items() if count > 1}

        self.assertEqual(duplicates, {})


if __name__ == "__main__":
    unittest.main()

