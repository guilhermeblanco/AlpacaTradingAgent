"""Tests for report rendering normalization.

Analyst reports arrive as free-form LLM markdown: tables collapsed onto one
line, section labels inlined, separator rows missing. These helpers repair
that before it reaches the browser, and a regression here shows up as a
mangled report rather than an error.
"""

from __future__ import annotations

import unittest

from webui.callbacks.report_callbacks import (
    _is_separator_row,
    _is_table_row,
    _normalize_table_block,
    _normalize_table_row,
    create_markdown_content,
    create_symbol_button,
    normalize_markdown_tables,
    normalize_market_markdown_sections,
)


class TableRowDetectionTests(unittest.TestCase):
    def test_a_row_needs_at_least_two_pipes(self):
        self.assertTrue(_is_table_row("| a | b |"))
        self.assertTrue(_is_table_row("a | b | c"))
        self.assertFalse(_is_table_row("a | b"))
        self.assertFalse(_is_table_row("plain prose"))

    def test_blank_lines_are_not_rows(self):
        self.assertFalse(_is_table_row(""))
        self.assertFalse(_is_table_row(None))

    def test_separator_rows_hold_only_dashes_and_colons(self):
        self.assertTrue(_is_separator_row("|---|---|"))
        self.assertTrue(_is_separator_row("| :--- | ---: |"))
        self.assertFalse(_is_separator_row("| a | b |"))

    def test_an_empty_pipe_row_is_not_a_separator(self):
        self.assertFalse(_is_separator_row("|  |  |"))
        self.assertFalse(_is_separator_row(""))


class TableRowNormalizationTests(unittest.TestCase):
    def test_cells_are_trimmed_and_re_delimited(self):
        self.assertEqual(_normalize_table_row("|a|  b  |c|"), "| a | b | c |")

    def test_a_row_without_outer_pipes_gains_them(self):
        self.assertEqual(_normalize_table_row("a | b"), "| a | b |")

    def test_an_all_empty_row_collapses_to_nothing(self):
        self.assertEqual(_normalize_table_row("|  |  |"), "")

    def test_a_separator_row_is_inserted_after_the_header(self):
        block = _normalize_table_block(["| Metric | Value |", "| RSI | 62 |"])

        self.assertEqual(block[0], "| Metric | Value |")
        self.assertTrue(_is_separator_row(block[1]))
        self.assertEqual(block[2], "| RSI | 62 |")

    def test_the_separator_matches_the_header_width(self):
        block = _normalize_table_block(["| a | b | c |", "| 1 | 2 | 3 |"])

        self.assertEqual(block[1].count("---"), 3)

    def test_an_existing_separator_is_not_duplicated(self):
        block = _normalize_table_block(
            ["| Metric | Value |", "| --- | --- |", "| RSI | 62 |"]
        )

        self.assertEqual(sum(_is_separator_row(row) for row in block), 1)

    def test_a_leading_title_is_kept_above_the_table(self):
        block = _normalize_table_block(["Summary table | Metric | Value |", "| RSI | 62 |"])

        self.assertEqual(block[0], "Summary table")
        self.assertTrue(_is_separator_row(block[2]))


class MarkdownTableNormalizationTests(unittest.TestCase):
    def test_empty_content_passes_through(self):
        self.assertEqual(normalize_markdown_tables(""), "")
        self.assertIsNone(normalize_markdown_tables(None))

    def test_a_well_formed_table_keeps_its_separator(self):
        content = "| Metric | Value |\n| --- | --- |\n| RSI | 62 |"

        result = normalize_markdown_tables(content)

        self.assertEqual(sum(_is_separator_row(line) for line in result.splitlines()), 1)

    def test_a_table_missing_its_separator_gains_one(self):
        result = normalize_markdown_tables("| Metric | Value |\n| RSI | 62 |")

        self.assertTrue(any(_is_separator_row(line) for line in result.splitlines()))

    def test_a_labelled_inline_table_is_split_onto_its_own_lines(self):
        result = normalize_markdown_tables("Summary: | Metric | Value | | RSI | 62 |")

        lines = [line for line in result.splitlines() if line.strip()]
        self.assertEqual(lines[0], "Summary")
        self.assertTrue(any(_is_separator_row(line) for line in lines))
        self.assertTrue(any("RSI" in line for line in lines))

    def test_notes_after_a_table_start_on_their_own_line(self):
        result = normalize_markdown_tables("| a | b |\n| --- | --- |\n| 1 | 2 | Notes: fine")

        self.assertIn("\nNotes", result)

    def test_prose_is_left_alone(self):
        content = "The market is trending up.\n\nMomentum remains positive."

        self.assertEqual(normalize_markdown_tables(content), content)

    def test_blank_lines_survive(self):
        """Dropping them runs every paragraph of a report together."""
        for content in (
            "First paragraph.\n\nSecond paragraph.",
            "## Conclusion\n\nThe trend is up.",
            "Signals:\n\n- RSI high\n- MACD cross",
        ):
            self.assertIn("\n\n", normalize_markdown_tables(content), content)

    def test_a_table_keeps_the_blank_line_that_precedes_it(self):
        content = "Momentum is positive.\n\n| Metric | Value |\n| RSI | 62 |"

        result = normalize_markdown_tables(content)

        self.assertIn("Momentum is positive.\n\n|", result)

    def test_output_is_stable_when_normalized_twice(self):
        """Reports get re-rendered on every refresh."""
        content = "| Metric | Value |\n| RSI | 62 |"

        once = normalize_markdown_tables(content)
        self.assertEqual(normalize_markdown_tables(once), once)


class MarketSectionNormalizationTests(unittest.TestCase):
    def test_empty_content_passes_through(self):
        self.assertEqual(normalize_market_markdown_sections(""), "")
        self.assertIsNone(normalize_market_markdown_sections(None))

    def test_lettered_section_labels_become_headings(self):
        result = normalize_market_markdown_sections("a) Conclusion The trend is up")

        self.assertIn("## Conclusion", result)

    def test_inline_colon_labels_become_headings(self):
        result = normalize_market_markdown_sections("Entry conditions: wait for a pullback")

        self.assertIn("## Entry Conditions", result)

    def test_labels_are_matched_case_insensitively(self):
        self.assertIn("## Invalidation", normalize_market_markdown_sections("INVALIDATION: below 400"))

    def test_every_known_section_is_recognized(self):
        for label, heading in (
            ("conclusion", "## Conclusion"),
            ("entry conditions", "## Entry Conditions"),
            ("invalidation", "## Invalidation"),
            ("risk sizing hint", "## Risk Sizing Hint"),
            ("narrative", "## Narrative"),
            ("summary table", "## Summary Table"),
        ):
            self.assertIn(heading, normalize_market_markdown_sections(f"{label}: body"))

    def test_the_final_proposal_is_pushed_onto_its_own_line(self):
        result = normalize_market_markdown_sections("Analysis done. FINAL TRANSACTION PROPOSAL: BUY")

        self.assertIn("\nFINAL TRANSACTION PROPOSAL:", result)
        self.assertIn("BUY", result)

    def test_runs_of_blank_lines_are_collapsed(self):
        result = normalize_market_markdown_sections("first\n\n\n\n\nsecond")

        self.assertNotIn("\n\n\n", result)

    def test_carriage_returns_are_normalized(self):
        self.assertNotIn("\r", normalize_market_markdown_sections("a\r\nb\rc"))

    def test_normalization_is_stable_when_applied_twice(self):
        content = "a) Conclusion The trend is up. Invalidation: below 400"

        once = normalize_market_markdown_sections(content)
        self.assertEqual(normalize_market_markdown_sections(once), once)


class SymbolButtonTests(unittest.TestCase):
    def test_button_is_addressed_by_index_within_the_reports_pager(self):
        rendered = str(create_symbol_button("NVDA", 2))

        self.assertIn("NVDA", rendered)
        self.assertIn("reports", rendered)

    def test_the_active_button_is_styled_differently(self):
        active = str(create_symbol_button("NVDA", 0, is_active=True))
        inactive = str(create_symbol_button("NVDA", 0, is_active=False))

        self.assertIn("'primary'", active)
        self.assertIn("outline-primary", inactive)
        self.assertNotEqual(active, inactive)


class MarkdownContentTests(unittest.TestCase):
    def test_empty_content_shows_the_default_message(self):
        rendered = str(create_markdown_content("", default_message="Nothing yet."))

        self.assertIn("Nothing yet.", rendered)

    def test_none_content_shows_the_default_message(self):
        self.assertIn("No content available yet.", str(create_markdown_content(None)))

    def test_a_loading_message_gets_no_debug_buttons(self):
        """Those buttons open prompts and tool output that do not exist yet."""
        for message in ("Loading market data...", "⏳ Waiting", "Analysis in progress"):
            rendered = str(create_markdown_content(message, report_type="market_report"))
            self.assertNotIn("show-prompt-btn", rendered, message)

    def test_real_content_with_a_report_type_gets_debug_buttons(self):
        rendered = str(
            create_markdown_content(
                "## Market Analysis\n\nMomentum is positive across the board.",
                report_type="market_report",
            )
        )

        self.assertIn("show-prompt-btn", rendered)
        self.assertIn("show-tool-outputs-btn", rendered)

    def test_real_content_without_a_report_type_gets_no_buttons(self):
        rendered = str(create_markdown_content("Momentum is positive."))

        self.assertNotIn("show-prompt-btn", rendered)

    def test_long_content_starting_with_loading_is_not_treated_as_a_status(self):
        """The status heuristic is length-bounded so a real report that opens
        with the word is still rendered as content."""
        content = "Loading indicators showed strength. " + ("Detail. " * 60)

        rendered = str(create_markdown_content(content, report_type="market_report"))

        self.assertIn("show-prompt-btn", rendered)

    def test_market_reports_get_section_normalization(self):
        rendered = str(
            create_markdown_content(
                "a) Conclusion The trend is up and momentum continues to build here.",
                report_type="market_report",
            )
        )

        self.assertIn("Conclusion", rendered)


if __name__ == "__main__":
    unittest.main()
