"""Tests for the three OpenAI web-search dataflow tools.

Social sentiment, global news, and fundamentals all reach the model the
same way: a Responses call with a hosted web_search tool for reasoning
models, plain chat completions otherwise. Each answers an analyst node
directly, so a failure has to come back as a usable briefing rather than
an exception.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from tradingagents.dataflows import interface

TOOLS = (
    ("get_stock_news_openai", ("NVDA", "2026-09-09")),
    ("get_global_news_openai", ("2026-09-09",)),
    ("get_fundamentals_openai", ("NVDA", "2026-09-09")),
)


class Client:
    """Records whichever API surface the tool chose."""

    def __init__(self, text="the briefing", error=None):
        self.responses_calls = []
        self.chat_calls = []
        self._text = text
        self._error = error
        outer = self

        class Responses:
            def create(self, **params):
                outer.responses_calls.append(params)
                if outer._error:
                    raise outer._error
                return SimpleNamespace(output_text=outer._text, output=[])

        class Completions:
            def create(self, **params):
                outer.chat_calls.append(params)
                if outer._error:
                    raise outer._error
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content=outer._text)
                        )
                    ]
                )

        self.responses = Responses()
        self.chat = SimpleNamespace(completions=Completions())


class WebSearchFixture(unittest.TestCase):
    MODEL = "gpt-5.4-nano"

    def setUp(self):
        # The empty/failed paths build a briefing out of the other news
        # sources; those have their own tests below, and reaching them here
        # would drag a rate-limit backoff into every case.
        for builder in (
            "_build_empty_openai_stock_news_fallback",
            "_build_empty_openai_global_fallback",
            "_build_empty_openai_fundamentals_fallback",
        ):
            patcher = mock.patch.object(
                interface,
                builder,
                lambda **kwargs: f"fallback briefing for {kwargs.get('curr_date')}",
            )
            patcher.start()
            self.addCleanup(patcher.stop)

    def _run(self, name, args, *, client=None, config=None, api_key="sk-test"):
        client = client if client is not None else Client()
        settings = {"quick_think_llm": self.MODEL}
        settings.update(config or {})

        with mock.patch.object(interface, "get_api_key", lambda *a: api_key):
            with mock.patch.object(interface, "get_config", lambda: settings):
                with mock.patch.object(
                    interface,
                    "get_openai_client_with_timeout",
                    lambda key, timeout_seconds=None: client,
                ):
                    return getattr(interface, name)(*args), client


class ApiKeyTests(WebSearchFixture):
    def test_each_tool_refuses_without_a_key(self):
        for name, args in TOOLS:
            answer, client = self._run(name, args, api_key=None)

            self.assertIn("OpenAI API key not found", answer)
            self.assertEqual(client.responses_calls, [])


class RoutingTests(WebSearchFixture):
    def test_a_reasoning_model_gets_the_hosted_web_search_tool(self):
        for name, args in TOOLS:
            _answer, client = self._run(name, args)

            self.assertEqual(len(client.responses_calls), 1, name)
            self.assertEqual(client.chat_calls, [], name)
            self.assertEqual(
                client.responses_calls[0]["tools"][0]["type"], "web_search", name
            )

    def test_a_gpt_41_model_also_gets_the_hosted_tool(self):
        for name, args in TOOLS:
            _answer, client = self._run(
                name, args, config={"quick_think_llm": "gpt-4.1-mini"}
            )

            self.assertEqual(len(client.responses_calls), 1, name)

    def test_an_older_model_falls_back_to_chat_completions(self):
        for name, args in TOOLS:
            _answer, client = self._run(
                name, args, config={"quick_think_llm": "gpt-4o-mini"}
            )

            self.assertEqual(len(client.chat_calls), 1, name)
            self.assertEqual(client.responses_calls, [], name)

    def test_the_chat_fallback_still_sends_a_system_and_user_turn(self):
        for name, args in TOOLS:
            _answer, client = self._run(
                name, args, config={"quick_think_llm": "gpt-4o-mini"}
            )

            roles = [m["role"] for m in client.chat_calls[0]["messages"]]
            self.assertEqual(roles, ["system", "user"], name)


class AnswerTests(WebSearchFixture):
    def test_the_model_answer_is_returned(self):
        for name, args in TOOLS:
            answer, _client = self._run(name, args, client=Client("the briefing"))

            self.assertEqual(answer, "the briefing", name)

    def test_an_empty_answer_becomes_a_stated_gap_not_an_empty_report(self):
        for name, args in TOOLS:
            answer, _client = self._run(name, args, client=Client(""))

            self.assertTrue(answer.strip(), name)
            self.assertIn("2026-09-09", answer, name)

    def test_a_failed_call_reports_the_cause_and_still_returns_a_briefing(self):
        for name, args in TOOLS:
            answer, _client = self._run(
                name, args, client=Client(error=RuntimeError("rate limited"))
            )

            self.assertIn("rate limited", answer, name)
            self.assertIn("2026-09-09", answer, name)

    def test_a_trailing_chat_offer_is_stripped(self):
        """The hosted search sometimes ends with an offer to continue, which
        would be read as analysis."""
        answer, _client = self._run(
            "get_stock_news_openai",
            ("NVDA", "2026-09-09"),
            client=Client("Sentiment is mixed.\n\nWould you like me to dig deeper?"),
        )

        self.assertIn("Sentiment is mixed.", answer)
        self.assertNotIn("dig deeper", answer)


class RequestShapeTests(WebSearchFixture):
    def _params(self, name, args, **kwargs):
        _answer, client = self._run(name, args, **kwargs)
        return client.responses_calls[0]

    def test_the_developer_and_user_turns_are_sent(self):
        for name, args in TOOLS:
            params = self._params(name, args)

            self.assertEqual(
                [turn["role"] for turn in params["input"]], ["developer", "user"], name
            )

    def test_a_non_reasoning_responses_model_uses_a_system_role(self):
        params = self._params(
            "get_stock_news_openai",
            ("NVDA", "2026-09-09"),
            config={"quick_think_llm": "gpt-4.1-mini"},
        )

        self.assertEqual(params["input"][0]["role"], "system")

    def test_the_search_sources_are_requested_back(self):
        for name, args in TOOLS:
            params = self._params(name, args)

            self.assertIn("web_search_call.action.sources", params["include"], name)

    def test_the_output_budget_is_configurable(self):
        params = self._params(
            "get_stock_news_openai",
            ("NVDA", "2026-09-09"),
            config={"stock_news_max_output_tokens": 321},
        )

        self.assertEqual(params["max_output_tokens"], 321)

    def test_responses_are_not_stored_by_default(self):
        """Prompts carry position and account context."""
        for name, args in TOOLS:
            self.assertFalse(self._params(name, args)["store"], name)

    def test_storing_responses_can_be_turned_on(self):
        for name, args in TOOLS:
            params = self._params(name, args, config={"openai_store_responses": True})

            self.assertTrue(params["store"], name)

    def test_the_fast_profile_narrows_the_search_context(self):
        params = self._params(
            "get_stock_news_openai",
            ("NVDA", "2026-09-09"),
            config={"stock_news_fast_profile": True},
        )

        self.assertEqual(params["tools"][0]["search_context_size"], "low")

    def test_turning_the_fast_profile_off_widens_it(self):
        params = self._params(
            "get_stock_news_openai",
            ("NVDA", "2026-09-09"),
            config={"stock_news_fast_profile": False, "research_depth": "Deep"},
        )

        self.assertNotEqual(params["tools"][0]["search_context_size"], "low")

    def test_an_over_budget_rejection_is_retried_without_the_cap(self):
        """Some models reject max_output_tokens outright."""
        attempts = []

        class Rejecting(Client):
            def __init__(self):
                super().__init__()
                outer = self

                class Responses:
                    def create(self, **params):
                        attempts.append(params)
                        if "max_output_tokens" in params:
                            raise ValueError("max_output_tokens is not supported")
                        return SimpleNamespace(output_text="ok", output=[])

                self.responses = Responses()

        answer, _client = self._run(
            "get_stock_news_openai", ("NVDA", "2026-09-09"), client=Rejecting()
        )

        self.assertEqual(answer, "ok")
        self.assertEqual(len(attempts), 2)
        self.assertNotIn("max_output_tokens", attempts[1])

    def test_an_unrelated_failure_is_not_retried(self):
        attempts = []

        class Failing(Client):
            def __init__(self):
                super().__init__()

                class Responses:
                    def create(self, **params):
                        attempts.append(params)
                        raise RuntimeError("rate limited")

                self.responses = Responses()

        answer, _client = self._run(
            "get_stock_news_openai", ("NVDA", "2026-09-09"), client=Failing()
        )

        self.assertEqual(len(attempts), 1)
        self.assertIn("rate limited", answer)


class PromptContentTests(WebSearchFixture):
    def _user_text(self, name, args, **kwargs):
        _answer, client = self._run(name, args, **kwargs)
        return client.responses_calls[0]["input"][1]["content"][0]["text"]

    def test_the_social_prompt_names_the_ticker_in_both_spellings(self):
        text = self._user_text("get_stock_news_openai", ("BTC/USD", "2026-09-09"))

        self.assertIn("BTC/USD", text)
        self.assertIn("BTCUSD", text)

    def test_the_lookback_window_widens_with_research_depth(self):
        shallow = self._user_text(
            "get_stock_news_openai",
            ("NVDA", "2026-09-09"),
            config={"research_depth": "Shallow"},
        )
        deep = self._user_text(
            "get_stock_news_openai",
            ("NVDA", "2026-09-09"),
            config={"research_depth": "Deep"},
        )

        self.assertIn("2026-09-06", shallow)
        self.assertIn("2026-08-26", deep)

    def test_the_global_prompt_targets_markets_when_no_symbol_is_given(self):
        text = self._user_text("get_global_news_openai", ("2026-09-09",))

        self.assertIn("markets", text.lower())

    def test_a_crypto_context_is_recognized_by_the_global_prompt(self):
        text = self._user_text("get_global_news_openai", ("2026-09-09", "BTC/USD"))

        self.assertIn("BTC/USD", text)

    def test_the_fundamentals_prompt_names_the_ticker(self):
        text = self._user_text("get_fundamentals_openai", ("NVDA", "2026-09-09"))

        self.assertIn("NVDA", text)


class QuickModelParamTests(unittest.TestCase):
    def test_the_store_setting_is_carried_through(self):
        """normalize_model_params always supplies a `store` default, so a
        setdefault here could never see the configured value."""
        for store in (True, False):
            params = interface._quick_model_params_for_tool(
                "gpt-5.4-nano",
                {},
                max_output_tokens=900,
                store_responses=store,
            )

            self.assertEqual(params["store"], store)

    def test_an_explicit_per_model_choice_wins(self):
        params = interface._quick_model_params_for_tool(
            "gpt-5.4-nano",
            {"quick_llm_params": {"store": True}},
            max_output_tokens=900,
            store_responses=False,
        )

        self.assertTrue(params["store"])

    def test_the_output_cap_is_applied_when_unset(self):
        params = interface._quick_model_params_for_tool(
            "gpt-5.4-nano", {}, max_output_tokens=321, store_responses=False
        )

        self.assertEqual(params["max_output_tokens"], 321)


if __name__ == "__main__":
    unittest.main()


class HeadlineCapTests(unittest.TestCase):
    """Fallback payloads are pasted into a prompt, so they are bounded."""

    def test_sections_beyond_the_cap_are_dropped(self):
        text = "\n".join(f"### headline {i}\nbody" for i in range(10))

        capped = interface._cap_headline_sections(text, max_sections=3)

        self.assertEqual(capped.count("### headline"), 3)

    def test_an_overlong_payload_is_clipped_with_an_ellipsis(self):
        capped = interface._cap_headline_sections("### h\n" + "x" * 500, max_chars=100)

        self.assertTrue(capped.endswith("..."))
        self.assertLessEqual(len(capped), 110)

    def test_empty_input_yields_an_empty_string(self):
        self.assertEqual(interface._cap_headline_sections(""), "")
        self.assertEqual(interface._cap_headline_sections(None), "")

    def test_content_under_both_caps_passes_through(self):
        self.assertEqual(
            interface._cap_headline_sections("### h\nbody"), "### h\nbody"
        )


class FallbackBriefingTests(unittest.TestCase):
    """When web search comes back empty, the other news sources stand in."""

    def _sources(self, **overrides):
        stubs = {
            "get_google_news": "### Google headline\nbody",
            "get_finnhub_news": "### Finnhub headline\nbody",
            "get_finnhub_company_insider_sentiment": "### 2026-8:\nChange: -1",
            "get_finnhub_company_insider_transactions": "### Filing Date: x\nChange: -1",
        }
        stubs.update(overrides)
        for name, value in stubs.items():
            patcher = mock.patch.object(interface, name, lambda *a, _v=value, **k: _v)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_stock_news_briefing_merges_both_sources(self):
        self._sources()

        briefing = interface._build_empty_openai_stock_news_fallback("NVDA", "2026-09-09")

        self.assertIn("Google News fallback", briefing)
        self.assertIn("Finnhub fallback", briefing)
        self.assertIn("Google headline", briefing)

    def test_the_stock_news_briefing_says_so_when_nothing_was_found(self):
        self._sources(get_google_news="", get_finnhub_news="")

        briefing = interface._build_empty_openai_stock_news_fallback("NVDA", "2026-09-09")

        self.assertIn("No dated stock-news items found for NVDA", briefing)

    def test_the_global_briefing_uses_google_news_as_a_macro_proxy(self):
        self._sources()

        briefing = interface._build_empty_openai_global_fallback("2026-09-09")

        self.assertIn("Global/Macro news proxy for global markets", briefing)
        self.assertIn("Google headline", briefing)

    def test_the_global_briefing_names_the_symbol_when_one_is_given(self):
        self._sources()

        briefing = interface._build_empty_openai_global_fallback("2026-09-09", "BTC/USD")

        self.assertIn("BTC/USD", briefing)

    def test_the_global_briefing_says_so_when_nothing_was_found(self):
        self._sources(get_google_news="")

        briefing = interface._build_empty_openai_global_fallback("2026-09-09")

        self.assertIn("No sufficiently relevant global-news items", briefing)

    def test_the_fundamentals_briefing_falls_back_to_insider_activity(self):
        self._sources()

        briefing = interface._build_empty_openai_fundamentals_fallback(
            "NVDA", "2026-09-09"
        )

        self.assertIn("Insider Sentiment Snapshot", briefing)
        self.assertIn("Insider Transactions Snapshot", briefing)
        self.assertIn("Recent Company News Snapshot", briefing)
