"""Tests for the design tokens.

The palette used to exist three times — a Python dict, hex literals in the
stylesheet, and one-off strings inline in components — so a colour change
had to be made in three places and usually was not. Status vocabulary was
worse: "completed", "done", "passed", and "healthy" all mean the same thing
and each panel picked its own green.
"""

from __future__ import annotations

import pathlib
import re
import unittest

from webui.config.constants import COLORS
from webui.config.tokens import (
    PALETTE,
    SPACING,
    STATUS_ALIASES,
    STATUS_TOKENS,
    TYPOGRAPHY,
    Status,
    css_variables,
    normalize_status,
    status_badge,
    status_color,
)


class PaletteTests(unittest.TestCase):
    def test_every_colour_is_a_hex_value(self):
        for name, value in PALETTE.items():
            self.assertRegex(value, r"^#[0-9A-Fa-f]{6}$", name)

    def test_the_palette_is_named_by_role_not_by_hue(self):
        """A theme change should be a change of value, not a rename."""
        for name in PALETTE:
            self.assertNotRegex(name, r"blue|green|red|amber|slate", name)

    def test_the_legacy_colour_dict_is_a_view_onto_the_palette(self):
        for value in COLORS.values():
            self.assertIn(value, PALETTE.values())

    def test_the_names_the_codebase_already_uses_still_resolve(self):
        for key in ("primary", "card", "background", "text", "error", "border"):
            self.assertIn(key, COLORS, key)


class StatusVocabularyTests(unittest.TestCase):
    def test_every_status_has_a_colour_and_a_badge(self):
        for status in STATUS_TOKENS:
            self.assertIn(status_color(status), PALETTE.values(), status)
            self.assertTrue(status_badge(status), status)

    def test_the_words_the_layers_use_map_onto_one_vocabulary(self):
        """Agents say "completed", stages say "done", gates say "passed"."""
        self.assertEqual(normalize_status("completed"), Status.DONE)
        self.assertEqual(normalize_status("passed"), Status.DONE)
        self.assertEqual(normalize_status("healthy"), Status.DONE)
        self.assertEqual(normalize_status("ok"), Status.DONE)

    def test_synonyms_are_drawn_the_same(self):
        self.assertEqual(status_color("completed"), status_color("passed"))
        self.assertEqual(status_color("in_progress"), status_color("running"))
        self.assertEqual(status_color("error"), status_color("failed"))

    def test_a_failure_and_a_block_are_both_negative(self):
        self.assertEqual(status_color("blocked"), PALETTE["negative"])
        self.assertEqual(status_color("failed"), PALETTE["negative"])

    def test_a_clip_is_a_caution_not_a_failure(self):
        """Size reduced is not size refused."""
        self.assertEqual(status_color("clipped"), PALETTE["caution"])
        self.assertNotEqual(status_color("clipped"), status_color("blocked"))

    def test_a_skipped_stage_is_dimmer_than_a_pending_one(self):
        self.assertNotEqual(status_color("skipped"), status_color("pending"))

    def test_the_vocabulary_is_case_and_space_insensitive(self):
        self.assertEqual(normalize_status("  COMPLETED "), Status.DONE)

    def test_an_unknown_word_falls_back_to_pending_rather_than_raising(self):
        self.assertEqual(normalize_status("something-new"), Status.PENDING)
        self.assertEqual(normalize_status(None), Status.PENDING)
        self.assertEqual(status_badge("something-new"), "secondary")

    def test_every_alias_points_at_a_real_status(self):
        for alias, target in STATUS_ALIASES.items():
            self.assertIn(target, STATUS_TOKENS, alias)


class CssVariableTests(unittest.TestCase):
    def test_every_palette_entry_becomes_a_custom_property(self):
        rendered = css_variables()

        for name, value in PALETTE.items():
            self.assertIn(f"--ta-{name}: {value};", rendered)

    def test_spacing_and_typography_are_emitted_too(self):
        rendered = css_variables()

        for name, value in SPACING.items():
            self.assertIn(f"--ta-space-{name}: {value};", rendered)
        for name, value in TYPOGRAPHY.items():
            self.assertIn(f"--ta-{name}: {value};", rendered)

    def test_each_status_gets_its_own_property(self):
        rendered = css_variables()

        for status in STATUS_TOKENS:
            self.assertIn(f"--ta-status-{status}:", rendered)

    def test_the_block_is_a_root_rule(self):
        rendered = css_variables()

        self.assertTrue(rendered.startswith(":root {"))
        self.assertTrue(rendered.endswith("}"))


class StylesheetTests(unittest.TestCase):
    def setUp(self):
        from webui.utils.styles import CSS

        self.css = CSS

    def test_the_tokens_are_emitted_into_the_stylesheet(self):
        self.assertIn("--ta-positive: #10B981;", self.css)

    def test_no_placeholder_survives_into_the_output(self):
        self.assertNotIn("__TOKENS__", self.css)

    def test_the_stylesheet_refers_to_tokens_rather_than_repeating_them(self):
        self.assertIn("var(--ta-surface)", self.css)
        self.assertIn("var(--ta-border)", self.css)

    def test_no_palette_colour_is_repeated_as_a_literal_outside_the_root_block(self):
        """A literal here is a colour that would not follow a theme change."""
        body = self.css.split("}", 1)[1]
        literals = {value.upper() for value in re.findall(r"#[0-9A-Fa-f]{6}", body)}

        self.assertEqual(literals & {value.upper() for value in PALETTE.values()}, set())

    def test_spacing_and_type_are_tokenized_in_the_workbench_blocks(self):
        self.assertIn("var(--ta-space-", self.css)
        self.assertIn("var(--ta-size-", self.css)

    def test_percent_literals_survive_interpolation(self):
        """Keyframes are full of them; %-formatting would have broken."""
        self.assertIn("0% { opacity", self.css)


class PanelVocabularyTests(unittest.TestCase):
    """Panels that used to keep their own colour maps now share one."""

    def test_the_tape_and_the_gate_ledger_agree_on_a_pass(self):
        from webui.components.workbench import GATE_STATUS_COLORS, STATE_COLORS

        self.assertEqual(STATE_COLORS["done"], GATE_STATUS_COLORS["passed"])

    def test_the_board_draws_a_halt_the_same_colour_as_the_tape(self):
        from webui.components.workbench import STATE_COLORS

        self.assertEqual(STATE_COLORS["blocked"], status_color("blocked"))

    def test_a_healthy_worker_is_the_same_green_as_a_completed_stage(self):
        from webui.components.workbench import STATE_COLORS

        self.assertEqual(status_color("ok"), STATE_COLORS["done"])

    def test_the_vitals_strip_colours_come_from_the_vocabulary(self):
        from webui.components.vitals import vital

        rendered = str(vital("Workers", "3/3 live", "ok"))

        self.assertIn(PALETTE["positive"], rendered)


if __name__ == "__main__":
    unittest.main()


class NoLiteralsLeftTests(unittest.TestCase):
    """Every colour goes through a token, so a theme can replace it.

    A hex value written into a stylesheet or an inline style belongs to
    one theme and silently survives into the other. These are the checks
    that keep a second theme possible at all.
    """

    ROOT = pathlib.Path(__file__).resolve().parent.parent

    def test_the_stylesheet_holds_no_colour_of_its_own(self):
        css = (self.ROOT / "webui" / "assets" / "custom.css").read_text()

        self.assertEqual(re.findall(r"#[0-9A-Fa-f]{3,8}\b", css), [])

    def test_no_alpha_colour_is_pinned_to_a_theme(self):
        """`rgba(59, 130, 246, 0.1)` names a colour; the token plus an
        alpha at the point of use names a role."""
        css = (self.ROOT / "webui" / "assets" / "custom.css").read_text()

        self.assertEqual(re.findall(r"rgba\([^)]*\)", css), [])

    def test_no_component_writes_a_colour_inline(self):
        for path in sorted((self.ROOT / "webui").rglob("*.py")):
            if path.name == "tokens.py":
                continue  # the palette itself, which is where hex belongs
            with self.subTest(module=path.relative_to(self.ROOT)):
                self.assertEqual(
                    re.findall(r'"#[0-9A-Fa-f]{6}"', path.read_text()),
                    [],
                    f"{path.name} pins a colour to one theme",
                )

    def test_every_token_the_stylesheet_uses_is_defined(self):
        from webui.config.tokens import css_variables
        from webui.utils.styles import CSS

        css = (self.ROOT / "webui" / "assets" / "custom.css").read_text()
        defined = set(re.findall(r"(--ta-[a-z0-9-]+)\s*:", css_variables()))
        used = set(re.findall(r"var\((--ta-[a-z0-9-]+)\)", css + CSS))

        self.assertEqual(sorted(used - defined), [])

    def test_every_colour_token_publishes_its_channels(self):
        """So an alpha variant can be built without naming the colour."""
        from webui.config.tokens import PALETTE, css_variables

        rendered = css_variables()

        for name in PALETTE:
            with self.subTest(token=name):
                self.assertIn(f"--ta-{name}-rgb:", rendered)

    def test_the_channels_match_the_colour(self):
        from webui.config.tokens import rgb_triplet

        self.assertEqual(rgb_triplet("#3B82F6"), "59 130 246")
        self.assertEqual(rgb_triplet("#000000"), "0 0 0")
        self.assertEqual(rgb_triplet("#FFFFFF"), "255 255 255")

