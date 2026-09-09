"""Tests for the shared trading-mode vocabulary.

Every agent asks this module what actions it is allowed to name, and the
graph reads the answer back out of free-form prose. The two directions have
to agree exactly: an extraction that misses the proposal line silently
turns a BUY into a default HOLD.
"""

from __future__ import annotations

import unittest

from tradingagents.agents.utils.agent_trading_modes import (
    TradingModeConfig,
    ensure_final_transaction_proposal,
    extract_recommendation,
    format_final_decision,
    get_agent_specific_context,
    get_position_transition,
    get_trading_mode_context,
    validate_recommendation,
)


class ModeSelectionTests(unittest.TestCase):
    def test_the_default_is_long_only_investment(self):
        context = get_trading_mode_context(None)

        self.assertEqual(context["mode"], "investment")
        self.assertFalse(context["allow_shorts"])
        self.assertEqual(context["action_list"], TradingModeConfig.INVESTMENT_ACTIONS)

    def test_allowing_shorts_switches_the_vocabulary(self):
        context = get_trading_mode_context({"allow_shorts": True})

        self.assertEqual(context["mode"], "trading")
        self.assertEqual(context["action_list"], TradingModeConfig.TRADING_ACTIONS)

    def test_the_current_position_shapes_the_trading_instructions(self):
        context = get_trading_mode_context({"allow_shorts": True}, "SHORT")

        self.assertEqual(context["current_position"], "SHORT")
        self.assertIn("SHORT", context["position_logic"])

    def test_the_final_format_matches_what_extraction_looks_for(self):
        """The prompt tells the model a shape; extraction reads that shape."""
        for config, action in (({}, "BUY"), ({"allow_shorts": True}, "LONG")):
            context = get_trading_mode_context(config)
            rendered = context["final_format"].replace(
                context["decision_format"], action
            )

            self.assertEqual(
                extract_recommendation(rendered, context["mode"]), action
            )


class AgentContextTests(unittest.TestCase):
    def test_each_agent_role_gets_its_own_instructions(self):
        context = get_trading_mode_context({})

        rendered = {
            role: get_agent_specific_context(role, context)
            for role in ("analyst", "researcher", "trader", "risk_mgmt", "manager")
        }

        self.assertEqual(len(set(rendered.values())), len(rendered))

    def test_an_unknown_role_falls_back_to_the_base_instructions(self):
        context = get_trading_mode_context({})

        self.assertEqual(
            get_agent_specific_context("historian", context), context["instructions"]
        )


class ExtractionTests(unittest.TestCase):
    def test_the_investment_proposal_line_is_read(self):
        for action in ("BUY", "HOLD", "SELL"):
            content = f"lots of analysis\n\nFINAL TRANSACTION PROPOSAL: **{action}**"

            self.assertEqual(extract_recommendation(content, "investment"), action)

    def test_the_trading_proposal_line_is_read(self):
        for action in ("LONG", "NEUTRAL", "SHORT"):
            content = f"analysis\n\nFINAL TRANSACTION PROPOSAL: **{action}**"

            self.assertEqual(extract_recommendation(content, "trading"), action)

    def test_the_alternate_investment_headings_are_read(self):
        for heading in ("FINAL INVESTMENT DECISION", "FINAL DECISION"):
            self.assertEqual(
                extract_recommendation(f"{heading}: **SELL**", "investment"), "SELL"
            )

    def test_lowercase_prose_is_still_read(self):
        self.assertEqual(
            extract_recommendation("final transaction proposal: **buy**", "investment"),
            "BUY",
        )

    def test_a_bolded_action_at_the_end_is_accepted_without_a_heading(self):
        self.assertEqual(
            extract_recommendation("The setup is clean, so **BUY**.", "investment"),
            "BUY",
        )

    def test_a_bolded_action_buried_early_is_not_mistaken_for_the_verdict(self):
        content = "**SELL** was last quarter's call. " + "x" * 300

        self.assertIsNone(extract_recommendation(content, "investment"))

    def test_prose_with_no_verdict_extracts_nothing(self):
        self.assertIsNone(extract_recommendation("Undecided.", "investment"))

    def test_a_trading_verdict_is_not_read_in_investment_mode(self):
        """LONG is not an action an investment-mode account can take."""
        content = "FINAL TRANSACTION PROPOSAL: **LONG**"

        self.assertIsNone(extract_recommendation(content, "investment"))


class ValidationTests(unittest.TestCase):
    def test_each_mode_accepts_only_its_own_actions(self):
        self.assertTrue(validate_recommendation("buy", "investment"))
        self.assertFalse(validate_recommendation("LONG", "investment"))
        self.assertTrue(validate_recommendation("short", "trading"))
        self.assertFalse(validate_recommendation("BUY", "trading"))

    def test_nothing_is_not_a_recommendation(self):
        self.assertFalse(validate_recommendation("", "investment"))
        self.assertFalse(validate_recommendation(None, "investment"))


class PositionTransitionTests(unittest.TestCase):
    def test_holding_the_same_side_is_a_hold(self):
        for side in ("LONG", "SHORT"):
            self.assertEqual(get_position_transition(side, side)["action"], "HOLD")

    def test_going_flat_closes_the_open_side(self):
        self.assertEqual(
            get_position_transition("LONG", "NEUTRAL")["action"], "CLOSE_LONG"
        )
        self.assertEqual(
            get_position_transition("SHORT", "NEUTRAL")["action"], "CLOSE_SHORT"
        )

    def test_flipping_sides_is_a_reversal_not_a_fresh_entry(self):
        """A reversal has to close the old leg before opening the new one."""
        transition = get_position_transition("LONG", "SHORT")

        self.assertEqual(transition["action"], "REVERSE_TO_SHORT")
        self.assertEqual(transition["new_position"], "SHORT")

    def test_opening_from_flat_is_an_entry(self):
        self.assertEqual(
            get_position_transition("NEUTRAL", "LONG")["action"], "OPEN_LONG"
        )
        self.assertEqual(
            get_position_transition("NEUTRAL", "SHORT")["action"], "OPEN_SHORT"
        )

    def test_staying_flat_does_nothing(self):
        self.assertEqual(
            get_position_transition("neutral", "neutral")["action"], "STAY_NEUTRAL"
        )

    def test_an_unrecognized_pair_is_reported_rather_than_guessed(self):
        transition = get_position_transition("LONG", "MAYBE")

        self.assertEqual(transition["action"], "UNKNOWN")
        self.assertIn("MAYBE", transition["description"])


class FinalLineTests(unittest.TestCase):
    def test_the_line_is_formatted_the_same_way_in_both_modes(self):
        self.assertEqual(
            format_final_decision("buy", "investment"),
            "FINAL TRANSACTION PROPOSAL: **BUY**",
        )
        self.assertEqual(
            format_final_decision("long", "trading"),
            "FINAL TRANSACTION PROPOSAL: **LONG**",
        )

    def test_no_recommendation_is_said_out_loud(self):
        self.assertIn("NO_RECOMMENDATION", format_final_decision("", "investment"))

    def test_the_line_is_appended_to_analysis_that_lacks_it(self):
        result = ensure_final_transaction_proposal(
            "Detailed analysis.", "BUY", "investment"
        )

        self.assertTrue(result.startswith("Detailed analysis."))
        self.assertTrue(result.endswith("FINAL TRANSACTION PROPOSAL: **BUY**"))

    def test_analysis_that_already_ends_correctly_is_left_alone(self):
        content = "Detailed analysis.\n\nFINAL TRANSACTION PROPOSAL: **BUY**"

        self.assertEqual(
            ensure_final_transaction_proposal(content, "BUY", "investment"), content
        )

    def test_empty_analysis_becomes_just_the_line(self):
        self.assertEqual(
            ensure_final_transaction_proposal("", "HOLD", "investment"),
            "FINAL TRANSACTION PROPOSAL: **HOLD**",
        )

    def test_the_appended_line_is_readable_back_out(self):
        result = ensure_final_transaction_proposal("Analysis.", "SELL", "investment")

        self.assertEqual(extract_recommendation(result, "investment"), "SELL")


if __name__ == "__main__":
    unittest.main()
