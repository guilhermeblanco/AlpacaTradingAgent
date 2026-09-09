"""Tests for the report completeness gate.

The UI streams analyst reports as they are written, so it needs to tell a
finished report from a half-written one. Calling a partial report finished
puts a truncated analysis in front of the user with no indication that more
is coming.
"""

from __future__ import annotations

import unittest

from webui.utils.report_validator import (
    get_report_completion_status,
    is_report_complete,
    validate_reports_for_ui,
)

TABLE = "| Key Metric | Value |\n|---|---|\n| RSI | 62 |"


def _long(text="Analysis. ", length=1200):
    return (text * (length // len(text) + 1))[:length]


class CompletenessTests(unittest.TestCase):
    def test_a_long_report_with_a_summary_heading_is_complete(self):
        content = _long() + "\n## Summary\nDone."

        self.assertTrue(is_report_complete(content, "market_report"))

    def test_any_of_the_closing_headings_counts(self):
        for heading in (
            "## Summary",
            "## Conclusion",
            "## Trading Implications",
            "## Recommendation",
            "**Recommendation:**",
            "## Key Points",
            "### Trading Implications",
        ):
            self.assertTrue(
                is_report_complete(_long() + "\n" + heading, "market_report"), heading
            )

    def test_a_markdown_table_alone_marks_a_report_complete(self):
        """A rendered table only appears once the analyst has finished."""
        content = "Only a few hundred characters of prose. " * 4 + "\n" + TABLE

        self.assertTrue(is_report_complete(content, "market_report"))

    def test_a_long_report_with_no_closing_structure_is_incomplete(self):
        self.assertFalse(is_report_complete(_long(), "market_report"))

    def test_a_closing_heading_on_a_short_report_is_not_enough(self):
        content = "## Summary\nStill writing..." + "x" * 200

        self.assertFalse(is_report_complete(content, "market_report"))

    def test_an_unlisted_report_type_has_a_lower_bar(self):
        """Only the five analyst reports are expected to run long."""
        content = "## Summary\n" + "x" * 400

        self.assertFalse(is_report_complete(content, "market_report"))
        self.assertTrue(is_report_complete(content, "trader_investment_plan"))

    def test_a_stub_is_never_complete(self):
        for content in ("", None, "   ", "too short"):
            self.assertFalse(is_report_complete(content, "market_report"), repr(content))

    def test_the_headings_are_matched_case_insensitively(self):
        content = _long() + "\n## summary\nDone."

        self.assertTrue(is_report_complete(content, "market_report"))


class UiValidationTests(unittest.TestCase):
    def test_a_complete_report_is_shown_verbatim(self):
        content = _long() + "\n## Summary\nDone."

        rendered = validate_reports_for_ui({"market_report": content})

        self.assertEqual(rendered["market_report"], content)

    def test_a_missing_report_says_so_by_name(self):
        rendered = validate_reports_for_ui({"sentiment_report": None})

        self.assertIn("Sentiment Report", rendered["sentiment_report"])
        self.assertIn("available yet", rendered["sentiment_report"])

    def test_a_partial_report_is_labelled_in_progress(self):
        rendered = validate_reports_for_ui({"market_report": _long()})

        self.assertIn("In Progress", rendered["market_report"])
        self.assertIn("Analysis currently running", rendered["market_report"])

    def test_a_partial_report_still_shows_a_preview(self):
        rendered = validate_reports_for_ui({"market_report": "Momentum is up. " * 5})

        self.assertIn("Momentum is up.", rendered["market_report"])

    def test_a_long_preview_is_truncated_with_an_ellipsis(self):
        rendered = validate_reports_for_ui({"market_report": _long()})

        self.assertIn("...", rendered["market_report"])

    def test_a_short_preview_is_not_padded_with_an_ellipsis(self):
        rendered = validate_reports_for_ui({"market_report": "Brief note."})

        self.assertIn("Brief note.", rendered["market_report"])
        self.assertNotIn("Brief note....", rendered["market_report"])

    def test_every_report_asked_about_comes_back(self):
        rendered = validate_reports_for_ui(
            {"market_report": None, "news_report": "partial", "macro_report": None}
        )

        self.assertEqual(
            set(rendered), {"market_report", "news_report", "macro_report"}
        )


class StatusTests(unittest.TestCase):
    def test_the_three_states_are_distinguished(self):
        status = get_report_completion_status(
            {
                "market_report": _long() + "\n## Summary",
                "news_report": "still writing",
                "macro_report": None,
            }
        )

        self.assertEqual(
            status,
            {
                "market_report": "complete",
                "news_report": "incomplete",
                "macro_report": "missing",
            },
        )

    def test_nothing_asked_about_yields_nothing(self):
        self.assertEqual(get_report_completion_status({}), {})


if __name__ == "__main__":
    unittest.main()
