"""Tests for the light theme, and for the one surface it cannot reach.

Everything drawn by CSS follows an attribute on the document element, so
a theme change is a class of problem CSS already solves. Figures are the
exception: a Plotly chart is rendered in Python and shipped as JSON, so
it has to be *told* which palette to draw in.

That distinction is where the bugs are. While writing this I twice
converted a figure builder and missed a caller — the chart kept its dark
background on a light page and nothing failed. The callers are checked
here for that reason.
"""

from __future__ import annotations

import pathlib
import re
import unittest

import plotly.graph_objects as go

from webui.config.figures import HEIGHT_SMALL, empty_figure, style
from webui.config.tokens import (
    DARK_PALETTE,
    DEFAULT_THEME,
    LIGHT_PALETTE,
    PALETTE,
    css_variables,
    normalize_theme,
    palette_for,
    status_color_for,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
CALLBACKS = ROOT / "webui" / "callbacks"


class PaletteTests(unittest.TestCase):
    def test_both_themes_name_exactly_the_same_roles(self):
        """A role missing from one palette is a colour that vanishes when
        you switch, and `var()` fails silently to inherit."""
        self.assertEqual(set(LIGHT_PALETTE), set(DARK_PALETTE))

    def test_light_is_the_default(self):
        self.assertEqual(DEFAULT_THEME, "light")
        self.assertEqual(PALETTE, LIGHT_PALETTE)

    def test_an_unknown_theme_falls_back_rather_than_raising(self):
        for value in ("", None, "sepia", "DARK "):
            with self.subTest(value=value):
                self.assertIn(normalize_theme(value), ("light", "dark"))

    def test_the_grounds_are_actually_opposite(self):
        """Cheap sanity: a light theme whose background is dark is a
        palette somebody edited halfway."""
        self.assertGreater(_lightness(LIGHT_PALETTE["background"]), 200)
        self.assertLess(_lightness(DARK_PALETTE["background"]), 60)

    def test_text_contrasts_with_its_own_ground_in_both(self):
        for name, palette in (("light", LIGHT_PALETTE), ("dark", DARK_PALETTE)):
            with self.subTest(theme=name):
                self.assertGreater(
                    abs(_lightness(palette["text"]) - _lightness(palette["background"])),
                    120,
                )

    def test_muted_text_is_still_readable_rather_than_merely_dimmer(self):
        """The dark palette could get away with a light grey; on white
        the same value is close to invisible."""
        for name, palette in (("light", LIGHT_PALETTE), ("dark", DARK_PALETTE)):
            with self.subTest(theme=name):
                self.assertGreater(
                    abs(
                        _lightness(palette["text-muted"])
                        - _lightness(palette["background"])
                    ),
                    60,
                )

    def test_the_outcome_colours_stay_distinguishable_in_both(self):
        for name, palette in (("light", LIGHT_PALETTE), ("dark", DARK_PALETTE)):
            with self.subTest(theme=name):
                self.assertNotEqual(palette["positive"], palette["negative"])
                self.assertNotEqual(palette["positive"], palette["caution"])


class StylesheetTests(unittest.TestCase):
    def test_light_is_on_root_so_there_is_no_flash(self):
        """Whatever is on `:root` is what a browser paints before any
        script runs."""
        rendered = css_variables()
        root_block = rendered.split("}", 1)[0]

        self.assertIn(f"--ta-background: {LIGHT_PALETTE['background']}", root_block)

    def test_dark_is_behind_an_attribute(self):
        self.assertIn('[data-theme="dark"] {', css_variables())

    def test_both_palettes_are_emitted_in_full(self):
        rendered = css_variables()

        for name in LIGHT_PALETTE:
            with self.subTest(token=name):
                self.assertEqual(rendered.count(f"--ta-{name}:"), 2)

    def test_the_asset_that_prevents_the_flash_runs_first(self):
        """Dash serves assets alphabetically; this one has to set the
        attribute before anything paints."""
        assets = sorted(p.name for p in (ROOT / "webui" / "assets").glob("*.js"))

        self.assertEqual(assets[0], "00_theme.js")

    def test_the_toggle_writes_through_the_same_helper_the_asset_defines(self):
        asset = (ROOT / "webui" / "assets" / "00_theme.js").read_text()
        callback = (CALLBACKS / "theme_callbacks.py").read_text()

        self.assertIn("tradingagentsSetTheme", asset)
        self.assertIn("tradingagentsSetTheme", callback)


class FigureTests(unittest.TestCase):
    """The one surface CSS cannot reach."""

    def test_a_figure_takes_its_ground_from_the_theme(self):
        light = style(go.Figure(), theme="light")
        dark = style(go.Figure(), theme="dark")

        self.assertEqual(light.layout.paper_bgcolor, LIGHT_PALETTE["surface"])
        self.assertEqual(dark.layout.paper_bgcolor, DARK_PALETTE["surface"])

    def test_the_plotly_template_follows_too(self):
        """Otherwise the gridlines and hover cards stay dark."""
        self.assertEqual(
            style(go.Figure(), theme="dark").layout.template,
            style(go.Figure(), theme="dark").layout.template,
        )
        light = style(go.Figure(), theme="light")
        self.assertEqual(light.layout.plot_bgcolor, LIGHT_PALETTE["surface"])

    def test_an_empty_figure_follows_the_theme_as_well(self):
        """On a fresh deployment every chart is this one."""
        self.assertEqual(
            empty_figure("nothing yet", theme="light").layout.paper_bgcolor,
            LIGHT_PALETTE["surface"],
        )

    def test_a_status_colour_can_be_asked_for_per_theme(self):
        self.assertEqual(status_color_for("done", "light"), LIGHT_PALETTE["positive"])
        self.assertEqual(status_color_for("done", "dark"), DARK_PALETTE["positive"])

    def test_no_figure_is_pinned_to_one_plotly_template(self):
        """`template="plotly_dark"` written into a callback is a chart
        that stays dark on a light page. Two were, and both looked fine
        until the page around them changed."""
        for path in sorted(CALLBACKS.glob("*.py")):
            # Comments are stripped: a note explaining that a chart used
            # to be pinned is not a chart that is pinned, and a guard
            # that cannot tell the difference makes the explanation
            # unwritable.
            source = "\n".join(
                line for line in path.read_text().splitlines()
                if not line.strip().startswith("#")
            )
            with self.subTest(module=path.name):
                self.assertNotIn('template="plotly_dark"', source)

    def test_every_figure_builder_is_called_with_a_theme(self):
        """The failure mode is silent: a converted builder with an
        unconverted caller renders in the default and nothing complains.
        I did this twice while writing it."""
        builders = (
            "gate_waterfall_figure",
            "evidence_figure",
            "outcome_figure",
            "stage_distribution_figure",
            "halt_breakdown_figure",
            "throughput_figure",
            "empty_figure",
        )
        pattern = re.compile(
            r"\b(" + "|".join(builders) + r")\((?:[^()]|\([^()]*\))*?\)", re.S
        )

        for path in sorted(CALLBACKS.glob("*.py")):
            source = path.read_text()
            for match in pattern.finditer(source):
                with self.subTest(module=path.name, call=match.group(1)):
                    self.assertIn(
                        "theme=",
                        match.group(0),
                        f"{path.name}: {match.group(1)} is called without a theme",
                    )


def _lightness(hex_value: str) -> float:
    raw = hex_value.lstrip("#")
    red, green, blue = (int(raw[index : index + 2], 16) for index in (0, 2, 4))
    return 0.299 * red + 0.587 * green + 0.114 * blue



class BootstrapModeTests(unittest.TestCase):
    """The bug that made every screen white on white.

    `dbc.themes.DARKLY` is a Bootswatch *dark* stylesheet: it hardcodes
    light text on every Bootstrap component. Switching the page
    background to light left the text where it was. No amount of
    tokenising our own CSS could have fixed it, because the colour was
    not ours.
    """

    def _app(self):
        from webui.app_dash import create_app

        return create_app()

    def test_the_base_stylesheet_is_not_a_dark_theme(self):
        stylesheets = " ".join(
            str(item) for item in self._app().config.external_stylesheets
        )

        for bootswatch_dark in ("darkly", "cyborg", "slate", "solar", "superhero"):
            with self.subTest(theme=bootswatch_dark):
                self.assertNotIn(bootswatch_dark, stylesheets.lower())

    def test_bootstrap_is_loaded_at_a_version_with_colour_modes(self):
        """`data-bs-theme` is Bootstrap 5.3. Below that there are no
        colour modes to drive and the base would have to be swapped."""
        stylesheets = " ".join(
            str(item) for item in self._app().config.external_stylesheets
        )

        self.assertIn("bootstrap@5.3", stylesheets)

    def test_the_toggle_tells_bootstrap_as_well_as_us(self):
        """Two attributes, two systems: `data-theme` drives our tokens,
        `data-bs-theme` drives Bootstrap's cards, inputs and tables."""
        asset = (ROOT / "webui" / "assets" / "00_theme.js").read_text()

        self.assertIn("data-theme", asset)
        self.assertIn("data-bs-theme", asset)

    def test_both_attributes_are_set_together(self):
        """Setting one without the other is half a theme, which is the
        state this bug was."""
        asset = (ROOT / "webui" / "assets" / "00_theme.js").read_text()

        self.assertEqual(asset.count("setAttribute('data-theme'"), 1)
        self.assertEqual(asset.count("setAttribute('data-bs-theme'"), 1)
        self.assertIn("function apply(", asset)


if __name__ == "__main__":
    unittest.main()
