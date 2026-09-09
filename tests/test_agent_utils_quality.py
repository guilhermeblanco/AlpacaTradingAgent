"""Tests for tool-output quality scoring and the semantic retry gate.

A web-search tool sometimes answers with a chat invitation instead of
research. These helpers decide whether that happened and whether paying for
a second call is worth it — so a wrong answer either ships a non-answer to
an analyst or doubles the cost of a good one.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from tradingagents.agents.utils import agent_utils as au


SUBSTANTIAL = "Solid research. " * 100


class MenuStrippingTests(unittest.TestCase):
    def test_a_trailing_offer_is_removed(self):
        text = "The market rallied.\n\nWould you like me to dig deeper?"

        self.assertEqual(
            au._strip_trailing_interactive_followup(text), "The market rallied."
        )

    def test_a_bulleted_menu_under_the_offer_goes_too(self):
        text = "Real analysis.\n\nWould you like me to:\n- compare peers\n- chart it\n"

        self.assertEqual(au._strip_trailing_interactive_followup(text), "Real analysis.")

    def test_a_numbered_menu_under_the_offer_goes_too(self):
        text = "Real analysis.\n\nWould you like me to:\n1. compare peers\n2. chart it\n"

        self.assertEqual(au._strip_trailing_interactive_followup(text), "Real analysis.")

    def test_bullets_in_the_body_are_kept(self):
        text = "Analysis.\n- a genuine finding\n\nMore analysis."

        self.assertEqual(au._strip_trailing_interactive_followup(text), text)

    def test_stripping_everything_keeps_the_original(self):
        text = "Would you like me to analyze this?"

        self.assertEqual(au._strip_trailing_interactive_followup(text), text)

    def test_empty_input_is_handled(self):
        self.assertEqual(au._strip_trailing_interactive_followup(""), "")
        self.assertEqual(au._strip_trailing_interactive_followup(None), "")

    def test_every_catalogued_pattern_is_stripped(self):
        for pattern in au.INTERACTIVE_FOLLOWUP_PATTERNS:
            text = f"Real analysis.\n\n{pattern} more?"

            self.assertEqual(
                au._strip_trailing_interactive_followup(text), "Real analysis.", pattern
            )


class FollowupDetectionTests(unittest.TestCase):
    def test_an_offer_at_the_end_is_detected(self):
        self.assertTrue(
            au._is_trailing_interactive_followup("Analysis. Would you like more?")
        )

    def test_plain_analysis_is_not(self):
        self.assertFalse(au._is_trailing_interactive_followup("The market rallied."))

    def test_empty_text_is_not(self):
        self.assertFalse(au._is_trailing_interactive_followup(""))
        self.assertFalse(au._is_trailing_interactive_followup(None))

    def test_only_the_tail_is_examined(self):
        """An offer buried early in a long report is not a trailing offer."""
        text = "Would you like me to start? " + ("Real analysis follows. " * 200)

        self.assertFalse(au._is_trailing_interactive_followup(text))


class QualityScoringTests(unittest.TestCase):
    def test_substantial_output_scores_clean(self):
        quality = au._score_output_quality("get_stock_news_openai", SUBSTANTIAL)

        self.assertEqual(quality["flags"], [])
        self.assertEqual(quality["score"], 1.0)
        self.assertFalse(quality["is_suspect"])

    def test_empty_output_is_flagged(self):
        quality = au._score_output_quality("get_stock_news_openai", "")

        self.assertIn("empty_output", quality["flags"])
        self.assertTrue(quality["is_suspect"])
        self.assertTrue(quality["retry_recommended"])

    def test_none_output_is_treated_as_empty(self):
        self.assertIn("empty_output", au._score_output_quality("any_tool", None)["flags"])

    def test_a_chat_invitation_instead_of_research_is_flagged(self):
        quality = au._score_output_quality(
            "get_stock_news_openai", "Would you like me to search for news?"
        )

        self.assertIn("interactive_followup", quality["flags"])
        self.assertTrue(quality["retry_recommended"])

    def test_a_long_report_that_merely_ends_with_an_offer_is_accepted(self):
        """Paying for a second search over a good answer is waste."""
        quality = au._score_output_quality(
            "get_stock_news_openai", SUBSTANTIAL + "\n\nWould you like more detail?"
        )

        self.assertNotIn("interactive_followup", quality["flags"])
        self.assertTrue(quality["trailing_interactive_followup"])

    def test_an_error_prefixed_output_is_flagged_but_not_retried(self):
        """Retrying a hard error just burns another call."""
        quality = au._score_output_quality("get_stock_news_openai", "Error: no such key")

        self.assertIn("error_prefixed_output", quality["flags"])
        self.assertFalse(quality["retry_recommended"])

    def test_output_below_the_tool_minimum_is_flagged(self):
        quality = au._score_output_quality("get_stock_news_openai", "too short")

        self.assertIn("undersized_output", quality["flags"])

    def test_a_legitimately_short_answer_is_not_undersized(self):
        for phrase in au.VALID_SHORT_OUTPUT_PATTERNS:
            quality = au._score_output_quality("get_stock_news_openai", phrase)

            self.assertNotIn("undersized_output", quality["flags"], phrase)

    def test_a_tool_without_a_minimum_is_never_undersized(self):
        quality = au._score_output_quality("some_other_tool", "brief")

        self.assertNotIn("undersized_output", quality["flags"])

    def test_the_score_falls_as_flags_accumulate(self):
        clean = au._score_output_quality("get_stock_news_openai", SUBSTANTIAL)["score"]
        empty = au._score_output_quality("get_stock_news_openai", "")["score"]

        self.assertGreater(clean, empty)
        self.assertGreaterEqual(empty, 0.0)

    def test_the_preview_is_bounded(self):
        quality = au._score_output_quality("any_tool", "x" * 5000)

        self.assertLessEqual(len(quality["output_preview"]), 220)
        self.assertEqual(quality["output_chars"], 5000)


class RetryDisabledToolTests(unittest.TestCase):
    def test_the_documented_defaults_apply(self):
        self.assertEqual(
            au._semantic_retry_disabled_tools({}),
            set(au.DEFAULT_SEMANTIC_RETRY_DISABLED_TOOLS),
        )

    def test_a_list_is_honoured(self):
        disabled = au._semantic_retry_disabled_tools(
            {"tool_semantic_retry_disabled_tools": ["a", " b "]}
        )

        self.assertEqual(disabled, {"a", "b"})

    def test_a_comma_separated_string_is_honoured(self):
        disabled = au._semantic_retry_disabled_tools(
            {"tool_semantic_retry_disabled_tools": "a, b ,"}
        )

        self.assertEqual(disabled, {"a", "b"})

    def test_an_explicit_none_disables_nothing(self):
        self.assertEqual(
            au._semantic_retry_disabled_tools(
                {"tool_semantic_retry_disabled_tools": None}
            ),
            set(),
        )

    def test_a_nonsense_value_falls_back_to_the_defaults(self):
        self.assertEqual(
            au._semantic_retry_disabled_tools({"tool_semantic_retry_disabled_tools": 5}),
            set(au.DEFAULT_SEMANTIC_RETRY_DISABLED_TOOLS),
        )


class RetryGateTests(unittest.TestCase):
    def _should_retry(self, tool_name="get_stock_news_openai", **overrides):
        params = {
            "uses_web_search": True,
            "semantic_retry_enabled": True,
            "retry_count": 0,
            "max_semantic_retries": 1,
            "quality": {"retry_recommended": True, "output_chars": 10},
            "config": {"tool_semantic_retry_disabled_tools": []},
        }
        params.update(overrides)
        return au._should_retry_tool_output(tool_name, **params)

    def test_a_suspect_web_search_result_is_retried(self):
        self.assertTrue(self._should_retry())

    def test_a_non_web_search_tool_is_not_retried(self):
        self.assertFalse(self._should_retry(uses_web_search=False))

    def test_retry_can_be_switched_off(self):
        self.assertFalse(self._should_retry(semantic_retry_enabled=False))

    def test_the_retry_budget_is_respected(self):
        self.assertFalse(self._should_retry(retry_count=1, max_semantic_retries=1))

    def test_a_clean_result_is_not_retried(self):
        self.assertFalse(
            self._should_retry(quality={"retry_recommended": False, "output_chars": 10})
        )

    def test_a_tool_on_the_disabled_list_is_not_retried(self):
        self.assertFalse(
            self._should_retry(
                config={"tool_semantic_retry_disabled_tools": ["get_stock_news_openai"]}
            )
        )

    def test_a_substantive_global_news_result_is_not_searched_twice(self):
        """The second global-news search is the expensive one."""
        minimum = au.TOOL_MIN_OUTPUT_CHARS["get_global_news_openai"]

        self.assertFalse(
            self._should_retry(
                "get_global_news_openai",
                quality={"retry_recommended": True, "output_chars": minimum + 1},
            )
        )

    def test_a_thin_global_news_result_is_retried(self):
        self.assertTrue(
            self._should_retry(
                "get_global_news_openai",
                quality={"retry_recommended": True, "output_chars": 1},
            )
        )


class ToolkitAvailabilityTests(unittest.TestCase):
    """Analysts choose their tools from these, so a wrong answer either hides
    a working source or offers one that cannot authenticate.

    Credentials resolve through get_api_key (encrypted vault, then
    environment), not through the config mapping, so that is the boundary
    these stub out. Toolkit(config=...) is avoided deliberately: it mutates
    class-level state shared by every other Toolkit in the process.
    """

    def _toolkit_with(self, keys):
        toolkit = au.Toolkit()
        patch = mock.patch.object(
            au, "get_api_key", lambda config_key, _env_key: keys.get(config_key)
        )
        patch.start()
        self.addCleanup(patch.stop)
        return toolkit

    def test_a_configured_key_reads_as_available(self):
        toolkit = self._toolkit_with({"finnhub_api_key": "real-key"})

        self.assertTrue(toolkit.has_finnhub())

    def test_a_missing_key_reads_as_unavailable(self):
        toolkit = self._toolkit_with({})

        self.assertFalse(toolkit.has_finnhub())

    def test_a_lookup_failure_reads_as_unavailable(self):
        """A broken vault must not present a source as usable."""
        toolkit = au.Toolkit()
        with mock.patch.object(
            au, "get_api_key", side_effect=RuntimeError("vault locked")
        ):
            self.assertFalse(toolkit.has_finnhub())

    def test_alpaca_needs_both_halves_of_its_credential(self):
        half = self._toolkit_with({"alpaca_api_key": "PK"})
        self.assertFalse(half.has_alpaca_credentials())

    def test_alpaca_with_both_halves_is_available(self):
        both = self._toolkit_with(
            {"alpaca_api_key": "PK", "alpaca_secret_key": "secret"}
        )
        self.assertTrue(both.has_alpaca_credentials())

    def test_each_source_reads_its_own_credential(self):
        for method, config_key in (
            ("has_openai_web_search", "openai_api_key"),
            ("has_finnhub", "finnhub_api_key"),
            ("has_fred", "fred_api_key"),
            ("has_coindesk", "coindesk_api_key"),
        ):
            toolkit = au.Toolkit()
            with mock.patch.object(
                au, "get_api_key", lambda key, _env, want=config_key: "x" if key == want else None
            ):
                self.assertTrue(getattr(toolkit, method)(), method)
            with mock.patch.object(au, "get_api_key", lambda *a, **k: None):
                self.assertFalse(getattr(toolkit, method)(), method)

    def test_market_data_requires_broker_credentials(self):
        """An Alpaca provider without keys cannot serve research data."""
        toolkit = au.Toolkit()
        provider = SimpleNamespace(name="alpaca", supports=lambda _s: True)

        with mock.patch(
            "tradingagents.marketdata.get_research_market_data_provider",
            lambda _config: provider,
        ), mock.patch.object(au, "get_api_key", lambda *a, **k: None):
            self.assertFalse(toolkit.has_research_market_data("NVDA"))

    def test_market_data_is_available_when_the_provider_supports_the_symbol(self):
        toolkit = au.Toolkit()
        provider = SimpleNamespace(name="tradier", supports=lambda _s: True)

        with mock.patch(
            "tradingagents.marketdata.get_research_market_data_provider",
            lambda _config: provider,
        ):
            self.assertTrue(toolkit.has_research_market_data("NVDA"))

    def test_an_unsupported_symbol_reads_as_unavailable(self):
        toolkit = au.Toolkit()
        provider = SimpleNamespace(name="tradier", supports=lambda _s: False)

        with mock.patch(
            "tradingagents.marketdata.get_research_market_data_provider",
            lambda _config: provider,
        ):
            self.assertFalse(toolkit.has_research_market_data("NVDA"))

    def test_a_provider_failure_reads_as_unavailable(self):
        toolkit = au.Toolkit()

        with mock.patch(
            "tradingagents.marketdata.get_research_market_data_provider",
            side_effect=RuntimeError("registry down"),
        ):
            self.assertFalse(toolkit.has_research_market_data("NVDA"))


if __name__ == "__main__":
    unittest.main()
