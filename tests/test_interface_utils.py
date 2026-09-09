"""Tests for the shared dataflow helpers.

These sit between the research depth a user picks and the request that
reaches a provider, and between a provider's response object and the text
an analyst reads. Both directions fail silently when they drift.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from tradingagents.dataflows import interface_utils as iu


class BoolCoercionTests(unittest.TestCase):
    def test_booleans_pass_through(self):
        self.assertTrue(iu._coerce_bool(True))
        self.assertFalse(iu._coerce_bool(False))

    def test_truthy_strings_are_recognized(self):
        for value in ("1", "true", "TRUE", " yes ", "on"):
            self.assertTrue(iu._coerce_bool(value), value)

    def test_other_strings_are_false(self):
        for value in ("0", "false", "no", "off", "", "maybe"):
            self.assertFalse(iu._coerce_bool(value), value)

    def test_non_strings_fall_back_to_truthiness(self):
        self.assertTrue(iu._coerce_bool(1))
        self.assertFalse(iu._coerce_bool(0))
        self.assertFalse(iu._coerce_bool(None))
        self.assertTrue(iu._coerce_bool(["item"]))


class TrailingFollowupTests(unittest.TestCase):
    """Web-search tools end with chat invitations that are not analysis."""

    def test_a_trailing_offer_is_removed(self):
        text = "The market rallied.\n\nWould you like me to dig deeper?"

        self.assertEqual(iu._strip_trailing_interactive_followup(text), "The market rallied.")

    def test_every_catalogued_pattern_is_stripped(self):
        for pattern in iu._TRAILING_INTERACTIVE_PATTERNS:
            text = f"Real analysis here.\n\n{pattern} something?"

            self.assertEqual(
                iu._strip_trailing_interactive_followup(text),
                "Real analysis here.",
                pattern,
            )

    def test_curly_quotes_and_dashes_still_match(self):
        text = "Real analysis here.\n\nIf you’d like, I can expand."

        self.assertEqual(
            iu._strip_trailing_interactive_followup(text), "Real analysis here."
        )

    def test_a_numbered_menu_under_the_offer_goes_too(self):
        text = (
            "Real analysis here.\n\n"
            "Would you like me to:\n"
            "1. compare peers\n"
            "2. chart the trend\n"
        )

        self.assertEqual(
            iu._strip_trailing_interactive_followup(text), "Real analysis here."
        )

    def test_bullets_in_the_body_are_kept(self):
        text = "Analysis.\n- a real finding\n\nMore analysis."

        self.assertEqual(iu._strip_trailing_interactive_followup(text), text)

    def test_a_bulleted_menu_under_the_offer_goes_too(self):
        text = (
            "Real analysis here.\n\n"
            "Would you like me to:\n"
            "- compare peers\n"
            "- chart the trend\n"
        )

        self.assertEqual(
            iu._strip_trailing_interactive_followup(text), "Real analysis here."
        )

    def test_analysis_without_an_offer_is_untouched(self):
        text = "The market rallied on heavy volume."

        self.assertEqual(iu._strip_trailing_interactive_followup(text), text)

    def test_stripping_everything_keeps_the_original(self):
        """Better to show a chatty answer than an empty report."""
        text = "Would you like me to analyze this?"

        self.assertEqual(iu._strip_trailing_interactive_followup(text), text)

    def test_empty_input_is_handled(self):
        self.assertEqual(iu._strip_trailing_interactive_followup(""), "")
        self.assertEqual(iu._strip_trailing_interactive_followup(None), "")

    def test_trailing_blank_lines_are_trimmed(self):
        self.assertEqual(
            iu._strip_trailing_interactive_followup("Analysis.\n\n\n"), "Analysis."
        )


class DepthMappingTests(unittest.TestCase):
    def test_each_depth_maps_to_a_search_context_size(self):
        self.assertEqual(iu.get_search_context_for_depth("shallow"), "low")
        self.assertEqual(iu.get_search_context_for_depth("medium"), "medium")
        self.assertEqual(iu.get_search_context_for_depth("deep"), "high")

    def test_depth_matching_ignores_case(self):
        self.assertEqual(iu.get_search_context_for_depth("DEEP"), "high")

    def test_an_unknown_depth_falls_back_to_medium(self):
        self.assertEqual(iu.get_search_context_for_depth("exhaustive"), "medium")

    def test_the_depth_comes_from_config_when_unspecified(self):
        with mock.patch.object(iu, "get_config", lambda: {"research_depth": "deep"}):
            self.assertEqual(iu.get_search_context_for_depth(), "high")

    def test_config_without_a_depth_falls_back_to_medium(self):
        with mock.patch.object(iu, "get_config", lambda: {}):
            self.assertEqual(iu.get_search_context_for_depth(), "medium")

    def test_effort_and_verbosity_track_the_depth(self):
        self.assertEqual(
            iu.get_llm_params_for_depth("shallow"), {"effort": "low", "verbosity": "low"}
        )
        self.assertEqual(
            iu.get_llm_params_for_depth("deep"), {"effort": "high", "verbosity": "high"}
        )

    def test_an_unknown_depth_gets_medium_effort(self):
        self.assertEqual(
            iu.get_llm_params_for_depth("exhaustive"),
            {"effort": "medium", "verbosity": "medium"},
        )

    def test_llm_params_read_config_when_unspecified(self):
        with mock.patch.object(iu, "get_config", lambda: {"research_depth": "shallow"}):
            self.assertEqual(iu.get_llm_params_for_depth()["effort"], "low")


class GlobalNewsProfileTests(unittest.TestCase):
    def test_the_fast_profile_stays_cheap_at_every_depth(self):
        """Deep research must not turn global news into a slow call."""
        for depth in ("shallow", "medium", "deep"):
            profile = iu.get_global_news_profile_for_depth(depth, fast_profile=True)

            self.assertEqual(profile["effort"], "low", depth)
            self.assertEqual(profile["search_context"], "low", depth)

    def test_the_fast_profile_still_widens_its_window_with_depth(self):
        windows = [
            iu.get_global_news_profile_for_depth(depth, fast_profile=True)["lookback_days"]
            for depth in ("shallow", "medium", "deep")
        ]

        self.assertEqual(windows, sorted(windows))
        self.assertLess(windows[0], windows[-1])

    def test_the_full_profile_scales_effort_with_depth(self):
        shallow = iu.get_global_news_profile_for_depth("shallow", fast_profile=False)
        deep = iu.get_global_news_profile_for_depth("deep", fast_profile=False)

        self.assertEqual(shallow["effort"], "low")
        self.assertEqual(deep["effort"], "high")
        self.assertEqual(deep["search_context"], "high")

    def test_the_full_profile_widens_the_window_further(self):
        deep_fast = iu.get_global_news_profile_for_depth("deep", fast_profile=True)
        deep_full = iu.get_global_news_profile_for_depth("deep", fast_profile=False)

        self.assertGreater(deep_full["lookback_days"], deep_fast["lookback_days"])

    def test_an_unknown_depth_falls_back_in_both_profiles(self):
        for fast in (True, False):
            profile = iu.get_global_news_profile_for_depth("exhaustive", fast_profile=fast)

            self.assertIn("lookback_days", profile)
            self.assertIn("effort", profile)

    def test_the_profile_reads_config_when_unspecified(self):
        with mock.patch.object(iu, "get_config", lambda: {"research_depth": "deep"}):
            profile = iu.get_global_news_profile_for_depth(fast_profile=False)

        self.assertEqual(profile["effort"], "high")


class ModelParamTests(unittest.TestCase):
    def test_reasoning_models_take_no_sampling_parameters(self):
        """gpt-5 rejects temperature, so it must not be sent."""
        params = iu.get_model_params("gpt-5-mini")

        self.assertNotIn("temperature", params)
        self.assertEqual(params, {})

    def test_the_5_2_family_asks_for_text_output_and_a_summary(self):
        params = iu.get_model_params("gpt-5.2")

        self.assertEqual(params["text"], {"format": "text"})
        self.assertEqual(params["summary"], "auto")
        self.assertEqual(params["reasoning"], {"effort": "medium"})

    def test_the_5_2_pro_variant_stores_instead_of_reasoning(self):
        params = iu.get_model_params("gpt-5.2-pro")

        self.assertTrue(params["store"])
        self.assertNotIn("reasoning", params)

    def test_gpt_4_1_uses_the_output_token_cap(self):
        params = iu.get_model_params("gpt-4.1", max_tokens_value=1234)

        self.assertEqual(params["max_output_tokens"], 1234)
        self.assertEqual(params["temperature"], 0.2)
        self.assertNotIn("max_tokens", params)

    def test_older_models_use_the_legacy_token_cap(self):
        params = iu.get_model_params("gpt-4o-mini", max_tokens_value=999)

        self.assertEqual(params["max_tokens"], 999)
        self.assertEqual(params["temperature"], 0.2)
        self.assertNotIn("max_output_tokens", params)


class ResponseExtractionTests(unittest.TestCase):
    def test_the_convenience_field_is_preferred(self):
        response = SimpleNamespace(output_text="  the answer  ")

        self.assertEqual(iu.extract_responses_text(response), "the answer")

    def test_no_response_yields_empty_text(self):
        self.assertEqual(iu.extract_responses_text(None), "")

    def test_nested_output_text_blocks_are_collected(self):
        response = SimpleNamespace(
            output=[
                {"content": [{"type": "output_text", "text": "first part"}]},
                {"content": [{"type": "output_text", "text": "second part"}]},
            ]
        )

        result = iu.extract_responses_text(response)

        self.assertIn("first part", result)
        self.assertIn("second part", result)

    def test_a_value_key_is_read_as_well_as_text(self):
        response = SimpleNamespace(output=[{"type": "text", "value": "from value"}])

        self.assertIn("from value", iu.extract_responses_text(response))

    def test_a_message_wrapper_is_unwrapped(self):
        response = SimpleNamespace(
            output=[{"message": {"content": [{"type": "output_text", "text": "inner"}]}}]
        )

        self.assertIn("inner", iu.extract_responses_text(response))

    def test_an_empty_convenience_field_falls_through_to_the_walk(self):
        response = SimpleNamespace(
            output_text="   ",
            output=[{"content": [{"type": "output_text", "text": "real text"}]}],
        )

        self.assertIn("real text", iu.extract_responses_text(response))

    def test_a_response_with_nothing_extractable_yields_empty_text(self):
        self.assertEqual(iu.extract_responses_text(SimpleNamespace(output=[])), "")

    def test_the_chat_completions_shape_is_a_fallback(self):
        response = SimpleNamespace(
            output=None,
            choices=[SimpleNamespace(message=SimpleNamespace(content="chat text"))],
        )

        self.assertEqual(iu.extract_responses_text(response), "chat text")

    def test_repeated_blocks_are_reported_once(self):
        response = SimpleNamespace(
            output=[
                {"content": [{"type": "output_text", "text": "same"}]},
                {"content": [{"type": "output_text", "text": "same"}]},
            ]
        )

        self.assertEqual(iu.extract_responses_text(response), "same")


class ClientTests(unittest.TestCase):
    def test_the_client_is_built_with_the_requested_timeout(self):
        """Web search runs long; the default SDK timeout cuts it off."""
        with mock.patch.object(iu, "OpenAI") as constructor:
            iu.get_openai_client_with_timeout("sk-test", timeout_seconds=120)

        timeout = constructor.call_args.kwargs["timeout"]
        self.assertEqual(constructor.call_args.kwargs["api_key"], "sk-test")
        self.assertEqual(timeout.read, 120)
        self.assertEqual(timeout.connect, 10.0)


if __name__ == "__main__":
    unittest.main()
