"""Tests for the dataflow tool surface.

`interface.py` is the boundary every analyst tool crosses. Each function
must return a string an LLM can read even when the source behind it is
empty, unreachable, or returning something unexpected — a raised exception
here aborts an analyst node.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from tradingagents.dataflows import interface


class HeadlineCappingTests(unittest.TestCase):
    """Fallback payloads are injected into prompts, so they stay bounded."""

    def test_empty_text_caps_to_empty(self):
        self.assertEqual(interface._cap_headline_sections(""), "")
        self.assertEqual(interface._cap_headline_sections(None), "")

    def test_text_under_the_limits_is_unchanged(self):
        text = "### One\n\nbody\n\n### Two\n\nbody"

        self.assertEqual(interface._cap_headline_sections(text), text)

    def test_sections_beyond_the_limit_are_dropped(self):
        text = "\n\n".join(f"### Item {index}\n\nbody" for index in range(20))

        capped = interface._cap_headline_sections(text, max_sections=3)

        self.assertEqual(capped.count("### "), 3)
        self.assertIn("Item 0", capped)
        self.assertNotIn("Item 5", capped)

    def test_an_overlong_section_is_clipped_with_an_ellipsis(self):
        text = "### One\n\n" + ("x" * 5000)

        capped = interface._cap_headline_sections(text, max_chars=100)

        self.assertLessEqual(len(capped), 120)
        self.assertTrue(capped.endswith("..."))

    def test_text_without_sections_is_kept(self):
        self.assertEqual(
            interface._cap_headline_sections("just prose"), "just prose"
        )


class NewsDedupeTests(unittest.TestCase):
    def test_the_same_story_normalizes_to_one_key(self):
        first = interface._normalize_news_dedupe_key(
            "  NVIDIA  Beats  ", "https://example.com/a/"
        )
        second = interface._normalize_news_dedupe_key(
            "nvidia beats", "https://EXAMPLE.com/a"
        )

        self.assertEqual(first, second)

    def test_different_stories_stay_distinct(self):
        self.assertNotEqual(
            interface._normalize_news_dedupe_key("A", "https://x/1"),
            interface._normalize_news_dedupe_key("B", "https://x/2"),
        )

    def test_missing_fields_are_tolerated(self):
        self.assertIsInstance(interface._normalize_news_dedupe_key(None, None), str)


class GoogleQueryExpansionTests(unittest.TestCase):
    """A bare ticker matches little; the company name widens the net."""

    def test_a_ticker_is_expanded_with_its_company_name(self):
        with mock.patch.object(
            interface.AlpacaUtils, "get_company_name", lambda _s: "NVIDIA Corp"
        ):
            expanded = interface._expand_google_news_query("NVDA")

        self.assertIn("NVDA", expanded)
        self.assertIn("$NVDA", expanded)
        self.assertIn("NVIDIA Corp", expanded)

    def test_aliases_are_split_into_alternatives(self):
        with mock.patch.object(
            interface.AlpacaUtils, "get_company_name", lambda _s: "Alphabet OR Google"
        ):
            expanded = interface._expand_google_news_query("GOOGL")

        self.assertIn("Alphabet", expanded)
        self.assertIn("Google", expanded)

    def test_a_crypto_pair_is_left_alone(self):
        self.assertEqual(interface._expand_google_news_query("BTC/USD"), "BTC/USD")

    def test_a_usd_suffixed_symbol_is_left_alone(self):
        self.assertEqual(interface._expand_google_news_query("BTCUSD"), "BTCUSD")

    def test_a_phrase_query_is_left_alone(self):
        query = "federal reserve interest rates"

        self.assertEqual(interface._expand_google_news_query(query), query)

    def test_an_empty_query_is_left_alone(self):
        self.assertEqual(interface._expand_google_news_query(""), "")

    def test_a_lookup_failure_leaves_the_bare_ticker(self):
        def explode(_symbol):
            raise RuntimeError("no credentials")

        with mock.patch.object(interface.AlpacaUtils, "get_company_name", explode):
            self.assertEqual(interface._expand_google_news_query("NVDA"), "NVDA")

    def test_a_name_matching_the_ticker_adds_nothing(self):
        with mock.patch.object(
            interface.AlpacaUtils, "get_company_name", lambda _s: "NVDA"
        ):
            self.assertEqual(interface._expand_google_news_query("NVDA"), "NVDA")


class GoogleNewsTests(unittest.TestCase):
    ITEM = {
        "title": "NVIDIA beats",
        "link": "https://example.com/a",
        "source": "Example",
        "date": "2026-09-08",
        "snippet": "Strong quarter.",
    }

    def _news(self, items):
        return mock.patch.object(interface, "getNewsData", lambda *a, **k: items)

    def test_no_results_yields_empty_text(self):
        with self._news([]):
            self.assertEqual(
                interface.get_google_news("NVDA", "2026-09-09", 7), ""
            )

    def test_results_are_rendered_as_sections(self):
        with self._news([self.ITEM]):
            rendered = interface.get_google_news("NVDA", "2026-09-09", 7)

        self.assertIn("### NVIDIA beats", rendered)
        self.assertIn("Strong quarter.", rendered)
        self.assertIn("Example", rendered)

    def test_duplicate_stories_are_reported_once(self):
        with self._news([self.ITEM, dict(self.ITEM)]):
            rendered = interface.get_google_news("NVDA", "2026-09-09", 7)

        self.assertEqual(rendered.count("### NVIDIA beats"), 1)
        self.assertIn("deduped from 2", rendered)

    def test_the_item_cap_is_honoured(self):
        items = [
            dict(self.ITEM, title=f"Story {index}", link=f"https://x/{index}")
            for index in range(50)
        ]

        with self._news(items), mock.patch.object(
            interface, "get_config", lambda: {"google_news_max_items": 5}
        ):
            rendered = interface.get_google_news("NVDA", "2026-09-09", 7)

        self.assertEqual(rendered.count("### Story"), 5)

    def test_a_missing_title_still_renders(self):
        with self._news([{"link": "https://x/1"}]):
            rendered = interface.get_google_news("NVDA", "2026-09-09", 7)

        self.assertIn("Untitled", rendered)


class WebSearchParamTests(unittest.TestCase):
    def test_reasoning_models_use_the_developer_role(self):
        params = interface._build_web_search_response_params(
            model="gpt-5.4-mini",
            developer_message="instructions",
            user_message="the question",
            search_context="low",
            max_output_tokens=900,
            store_responses=False,
            model_params={},
        )

        self.assertEqual(params["input"][0]["role"], "developer")

    def test_the_web_search_tool_is_always_attached(self):
        params = interface._build_web_search_response_params(
            model="gpt-4.1",
            developer_message="instructions",
            user_message="the question",
            search_context="high",
            max_output_tokens=900,
            store_responses=False,
            model_params={},
        )

        self.assertEqual(params["tools"][0]["type"], "web_search")
        self.assertEqual(params["tools"][0]["search_context_size"], "high")

    def test_encrypted_reasoning_is_requested_only_when_asked(self):
        without = interface._build_web_search_response_params(
            model="gpt-5.4-mini",
            developer_message="d",
            user_message="u",
            search_context="low",
            max_output_tokens=900,
            store_responses=False,
            model_params={},
        )
        with_reasoning = interface._build_web_search_response_params(
            model="gpt-5.4-mini",
            developer_message="d",
            user_message="u",
            search_context="low",
            max_output_tokens=900,
            store_responses=False,
            model_params={},
            include_reasoning=True,
        )

        self.assertNotIn("reasoning.encrypted_content", without["include"])
        self.assertIn("reasoning.encrypted_content", with_reasoning["include"])

    def test_the_store_flag_survives_for_every_model_family(self):
        for model in ("gpt-5.4-mini", "gpt-4.1"):
            params = interface._build_web_search_response_params(
                model=model,
                developer_message="d",
                user_message="u",
                search_context="low",
                max_output_tokens=900,
                store_responses=True,
                model_params={},
            )

            self.assertIn("store", params, model)

    def test_hosted_web_search_is_selected_per_model_family(self):
        self.assertTrue(interface._uses_responses_for_web_search("gpt-4.1"))
        self.assertFalse(interface._uses_responses_for_web_search(""))

    def test_quick_params_carry_the_caller_defaults(self):
        params = interface._quick_model_params_for_tool(
            "gpt-5.4-nano", {}, max_output_tokens=1234, store_responses=True
        )

        self.assertEqual(params["max_output_tokens"], 1234)

    def test_the_store_setting_reaches_the_tool_call(self):
        """normalize_model_params always supplies a store default, so this
        only works if the caller's value is applied over it."""
        for store in (True, False):
            params = interface._quick_model_params_for_tool(
                "gpt-5.4-nano", {}, max_output_tokens=900, store_responses=store
            )

            self.assertEqual(params["store"], store, store)

    def test_an_explicit_per_model_store_choice_wins(self):
        params = interface._quick_model_params_for_tool(
            "gpt-5.4-nano",
            {"quick_llm_params": {"store": False}},
            max_output_tokens=900,
            store_responses=True,
        )

        self.assertFalse(params["store"])

    def test_configured_quick_params_win_over_the_defaults(self):
        params = interface._quick_model_params_for_tool(
            "gpt-5.4-nano",
            {"quick_llm_params": {"max_output_tokens": 42}},
            max_output_tokens=1234,
            store_responses=False,
        )

        self.assertEqual(params["max_output_tokens"], 42)


class OutputCapFallbackTests(unittest.TestCase):
    """Some models reject max_output_tokens; the call is retried without it."""

    def test_a_successful_call_is_returned_directly(self):
        client = mock.MagicMock()
        client.responses.create.return_value = "the response"

        result = interface._create_response_with_output_cap_fallback(
            client, {"model": "gpt-5.4-mini", "max_output_tokens": 900}
        )

        self.assertEqual(result, "the response")
        self.assertEqual(client.responses.create.call_count, 1)

    def test_a_token_cap_rejection_is_retried_without_the_cap(self):
        client = mock.MagicMock()
        client.responses.create.side_effect = [
            ValueError("max_output_tokens is not supported"),
            "the response",
        ]

        result = interface._create_response_with_output_cap_fallback(
            client, {"model": "gpt-5.4-mini", "max_output_tokens": 900}
        )

        self.assertEqual(result, "the response")
        retry = client.responses.create.call_args_list[1].kwargs
        self.assertNotIn("max_output_tokens", retry)

    def test_an_unrelated_failure_is_not_retried(self):
        client = mock.MagicMock()
        client.responses.create.side_effect = RuntimeError("auth failed")

        with self.assertRaises(RuntimeError):
            interface._create_response_with_output_cap_fallback(
                client, {"model": "gpt-5.4-mini", "max_output_tokens": 900}
            )

        self.assertEqual(client.responses.create.call_count, 1)

    def test_a_call_without_a_cap_is_not_retried(self):
        client = mock.MagicMock()
        client.responses.create.side_effect = ValueError("max_output_tokens bad")

        with self.assertRaises(ValueError):
            interface._create_response_with_output_cap_fallback(
                client, {"model": "gpt-5.4-mini"}
            )


class EmptySearchFallbackTests(unittest.TestCase):
    """When web search comes back empty the analyst still needs evidence."""

    def test_the_global_fallback_labels_itself(self):
        with mock.patch.object(
            interface, "get_google_news", lambda **k: "### A headline\n\nbody"
        ):
            text = interface._build_empty_openai_global_fallback("2026-09-09")

        self.assertIn("Fallback used because OpenAI", text)
        self.assertIn("A headline", text)

    def test_the_global_fallback_says_so_when_nothing_is_found(self):
        with mock.patch.object(interface, "get_google_news", lambda **k: ""):
            text = interface._build_empty_openai_global_fallback("2026-09-09", "NVDA")

        self.assertIn("No sufficiently relevant", text)
        self.assertIn("NVDA", text)

    def test_the_stock_fallback_merges_every_available_source(self):
        with mock.patch.object(
            interface, "get_google_news", lambda **k: "### G\n\ngoogle body"
        ), mock.patch.object(
            interface, "get_finnhub_news", lambda **k: "### F\n\nfinnhub body"
        ):
            text = interface._build_empty_openai_stock_news_fallback(
                "NVDA", "2026-09-09"
            )

        self.assertIn("Google News fallback", text)
        self.assertIn("Finnhub fallback", text)

    def test_the_stock_fallback_says_so_when_every_source_is_empty(self):
        with mock.patch.object(
            interface, "get_google_news", lambda **k: ""
        ), mock.patch.object(interface, "get_finnhub_news", lambda **k: ""):
            text = interface._build_empty_openai_stock_news_fallback(
                "NVDA", "2026-09-09"
            )

        self.assertIn("No fallback stock-news items", text)

    def test_the_fundamentals_fallback_covers_each_snapshot(self):
        with mock.patch.object(
            interface, "get_finnhub_company_insider_sentiment", lambda *a: "sentiment"
        ), mock.patch.object(
            interface, "get_finnhub_company_insider_transactions", lambda *a: "tx"
        ), mock.patch.object(interface, "get_finnhub_news", lambda *a: "news"):
            text = interface._build_empty_openai_fundamentals_fallback(
                "NVDA", "2026-09-09"
            )

        self.assertIn("Insider Sentiment Snapshot", text)
        self.assertIn("Insider Transactions Snapshot", text)
        self.assertIn("Recent Company News Snapshot", text)


if __name__ == "__main__":
    unittest.main()
