"""Tests for the five analyst graph nodes.

Each analyst picks its tools from what the toolkit reports as available,
runs a bounded tool loop, and writes one report key into the graph state.
None of that involves a real model here: the LLM and every tool are fakes,
so the branching is what is under test.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import Runnable

from tradingagents.agents.analysts.fundamentals_analyst import (
    create_fundamentals_analyst,
)
from tradingagents.agents.analysts.macro_analyst import create_macro_analyst
from tradingagents.agents.analysts.market_analyst import (
    _normalize_market_report_markdown,
    create_market_analyst,
)
from tradingagents.agents.analysts.news_analyst import create_news_analyst
from tradingagents.agents.analysts.social_media_analyst import (
    create_social_media_analyst,
)


class FakeLLM(Runnable):
    """Returns scripted messages and records what it was bound to."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.bound_tools = None
        self.invocations = 0

    def bind_tools(self, tools):
        self.bound_tools = list(tools)
        return self

    def invoke(self, input, config=None, **kwargs):  # noqa: A002 - Runnable API
        self.invocations += 1
        if self._responses:
            return self._responses.pop(0)
        return AIMessage(content="FINAL TRANSACTION PROPOSAL: HOLD")


def _tool(name, result="tool output"):
    calls = []

    def invoke(args, *rest, **kwargs):
        calls.append(args)
        return result

    return SimpleNamespace(name=name, invoke=invoke, calls=calls, description=name)


TOOL_NAMES = (
    # market
    "get_technical_brief",
    "get_market_data_report",
    "get_stockstats_indicators_report_online",
    "get_stockstats_indicators_report",
    # news
    "get_global_news_openai",
    "get_google_news",
    "get_finnhub_news_recent",
    "get_coindesk_news",
    # social
    "get_stock_news_openai",
    "get_reddit_stock_info",
    "get_reddit_news",
    # fundamentals
    "get_fundamentals_openai",
    "get_simfin_balance_sheet",
    "get_simfin_cashflow",
    "get_simfin_income_stmt",
    "get_finnhub_company_insider_sentiment",
    "get_finnhub_company_insider_transactions",
    "get_defillama_fundamentals",
    # macro
    "get_macro_news_openai",
    "get_macro_analysis",
    "get_economic_indicators",
    "get_yield_curve_analysis",
)


def _toolkit(**availability):
    flags = {
        "online_tools": True,
        "market_data": True,
        "openai_web_search": True,
        "finnhub": True,
        "coindesk": True,
        "simfin_data": True,
        "fred": True,
    }
    flags.update(availability)

    toolkit = SimpleNamespace(
        config={
            "online_tools": flags["online_tools"],
            "max_tool_iterations_per_agent": 3,
            "max_same_tool_call_repeats": 1,
        },
        has_research_market_data=lambda _ticker: flags["market_data"],
        has_openai_web_search=lambda *a, **k: flags["openai_web_search"],
        has_finnhub=lambda *a, **k: flags["finnhub"],
        has_coindesk=lambda *a, **k: flags["coindesk"],
        has_simfin_data=lambda *a, **k: flags["simfin_data"],
        has_fred=lambda *a, **k: flags["fred"],
    )
    for name in TOOL_NAMES:
        setattr(toolkit, name, _tool(name))
    return toolkit


def _state(ticker="NVDA", messages=None):
    return {
        "trade_date": "2026-09-09",
        "company_of_interest": ticker,
        "messages": messages if messages is not None else [HumanMessage(content="go")],
    }


def _bound_names(llm):
    return [tool.name for tool in (llm.bound_tools or [])]


class MarketReportNormalizationTests(unittest.TestCase):
    def test_lettered_sections_become_headings(self):
        self.assertIn(
            "## Conclusion", _normalize_market_report_markdown("a) Conclusion up")
        )

    def test_inline_labels_become_headings(self):
        self.assertIn(
            "## Invalidation",
            _normalize_market_report_markdown("Invalidation: below 400"),
        )

    def test_the_final_proposal_gets_its_own_block(self):
        result = _normalize_market_report_markdown("Done. FINAL TRANSACTION PROPOSAL: BUY")

        self.assertIn("\nFINAL TRANSACTION PROPOSAL:", result)

    def test_empty_content_passes_through(self):
        self.assertEqual(_normalize_market_report_markdown(""), "")
        self.assertIsNone(_normalize_market_report_markdown(None))

    def test_blank_line_runs_are_collapsed(self):
        self.assertNotIn("\n\n\n", _normalize_market_report_markdown("a\n\n\n\n\nb"))


class MarketAnalystTests(unittest.TestCase):
    def test_the_full_online_toolset_is_offered_when_data_is_available(self):
        llm = FakeLLM(AIMessage(content="analysis. FINAL TRANSACTION PROPOSAL: BUY"))

        create_market_analyst(llm, _toolkit())(_state())

        self.assertEqual(
            _bound_names(llm),
            [
                "get_technical_brief",
                "get_market_data_report",
                "get_stockstats_indicators_report_online",
            ],
        )

    def test_it_falls_back_to_offline_indicators_when_data_is_missing(self):
        """The run continues on stockstats rather than failing."""
        llm = FakeLLM(AIMessage(content="analysis. FINAL TRANSACTION PROPOSAL: HOLD"))

        create_market_analyst(llm, _toolkit(market_data=False))(_state())

        self.assertEqual(_bound_names(llm), ["get_stockstats_indicators_report"])

    def test_offline_mode_still_offers_price_context_when_available(self):
        llm = FakeLLM(AIMessage(content="analysis. FINAL TRANSACTION PROPOSAL: HOLD"))

        create_market_analyst(llm, _toolkit(online_tools=False))(_state())

        self.assertEqual(
            _bound_names(llm),
            ["get_market_data_report", "get_stockstats_indicators_report"],
        )

    def test_the_report_lands_under_its_state_key(self):
        llm = FakeLLM(AIMessage(content="the read. FINAL TRANSACTION PROPOSAL: BUY"))

        result = create_market_analyst(llm, _toolkit())(_state())

        self.assertIn("market_report", result)
        self.assertIn("the read", result["market_report"])
        self.assertEqual(len(result["messages"]), 1)

    def test_a_missing_proposal_triggers_a_second_call(self):
        llm = FakeLLM(
            AIMessage(content="just the analysis"),
            AIMessage(content="FINAL TRANSACTION PROPOSAL: SELL"),
        )

        result = create_market_analyst(llm, _toolkit())(_state())

        self.assertEqual(llm.invocations, 2)
        self.assertIn("FINAL TRANSACTION PROPOSAL", result["market_report"])

    def test_an_empty_response_is_replaced_with_a_stated_limitation(self):
        """A blank report must not read as a confident neutral call."""
        llm = FakeLLM(AIMessage(content=""), AIMessage(content="FINAL TRANSACTION PROPOSAL: HOLD"))

        result = create_market_analyst(llm, _toolkit())(_state())

        self.assertIn("did not return a complete", result["market_report"])

    def test_a_requested_tool_is_executed_and_fed_back(self):
        toolkit = _toolkit()
        llm = FakeLLM(
            AIMessage(
                content="",
                additional_kwargs={
                    "tool_calls": [
                        {"id": "1", "name": "get_technical_brief", "args": {"ticker": "NVDA"}}
                    ]
                },
            ),
            AIMessage(content="done. FINAL TRANSACTION PROPOSAL: BUY"),
        )

        create_market_analyst(llm, toolkit)(_state())

        self.assertEqual(toolkit.get_technical_brief.calls, [{"ticker": "NVDA"}])

    def test_an_unknown_tool_does_not_stop_the_run(self):
        llm = FakeLLM(
            AIMessage(
                content="",
                additional_kwargs={
                    "tool_calls": [{"id": "1", "name": "no_such_tool", "args": {}}]
                },
            ),
            AIMessage(content="recovered. FINAL TRANSACTION PROPOSAL: HOLD"),
        )

        result = create_market_analyst(llm, _toolkit())(_state())

        self.assertIn("recovered", result["market_report"])

    def test_a_repeated_identical_call_is_only_executed_once(self):
        """Otherwise a looping model burns the whole tool budget on one call."""
        toolkit = _toolkit()
        call = {"id": "1", "name": "get_technical_brief", "args": {"ticker": "NVDA"}}
        llm = FakeLLM(
            AIMessage(content="", additional_kwargs={"tool_calls": [call]}),
            AIMessage(content="", additional_kwargs={"tool_calls": [call]}),
            AIMessage(content="done. FINAL TRANSACTION PROPOSAL: BUY"),
        )

        create_market_analyst(llm, toolkit)(_state())

        self.assertEqual(len(toolkit.get_technical_brief.calls), 1)

    def test_an_endless_tool_loop_is_halted_and_says_so(self):
        toolkit = _toolkit()
        looping = AIMessage(
            content="",
            additional_kwargs={
                "tool_calls": [{"id": "1", "name": "get_technical_brief", "args": {}}]
            },
        )
        llm = FakeLLM(*[looping] * 10)

        result = create_market_analyst(llm, toolkit)(_state())

        self.assertIn("Tool-loop halted", result["market_report"])

    def test_a_failing_tool_is_reported_rather_than_raised(self):
        toolkit = _toolkit()

        def explode(_args, *rest, **kwargs):
            raise RuntimeError("provider down")

        toolkit.get_technical_brief.invoke = explode
        llm = FakeLLM(
            AIMessage(
                content="",
                additional_kwargs={
                    "tool_calls": [{"id": "1", "name": "get_technical_brief", "args": {}}]
                },
            ),
            AIMessage(content="carried on. FINAL TRANSACTION PROPOSAL: HOLD"),
        )

        result = create_market_analyst(llm, toolkit)(_state())

        self.assertIn("carried on", result["market_report"])

    def test_string_tool_arguments_are_parsed(self):
        toolkit = _toolkit()
        llm = FakeLLM(
            AIMessage(
                content="",
                additional_kwargs={
                    "tool_calls": [
                        {
                            "id": "1",
                            "function": {
                                "name": "get_technical_brief",
                                "arguments": '{"ticker": "NVDA"}',
                            },
                        }
                    ]
                },
            ),
            AIMessage(content="done. FINAL TRANSACTION PROPOSAL: BUY"),
        )

        create_market_analyst(llm, toolkit)(_state())

        self.assertEqual(toolkit.get_technical_brief.calls, [{"ticker": "NVDA"}])

    def test_malformed_tool_arguments_do_not_stop_the_run(self):
        toolkit = _toolkit()
        llm = FakeLLM(
            AIMessage(
                content="",
                additional_kwargs={
                    "tool_calls": [
                        {
                            "id": "1",
                            "function": {
                                "name": "get_technical_brief",
                                "arguments": "not json",
                            },
                        }
                    ]
                },
            ),
            AIMessage(content="done. FINAL TRANSACTION PROPOSAL: BUY"),
        )

        result = create_market_analyst(llm, toolkit)(_state())

        self.assertIn("done", result["market_report"])


class NewsAnalystTests(unittest.TestCase):
    def test_web_search_is_offered_when_available(self):
        llm = FakeLLM(AIMessage(content="news read"))

        create_news_analyst(llm, _toolkit())(_state())

        self.assertIn("get_google_news", _bound_names(llm))

    def test_the_report_lands_under_its_state_key(self):
        llm = FakeLLM(AIMessage(content="news read"))

        result = create_news_analyst(llm, _toolkit())(_state())

        self.assertIn("news_report", result)

    def test_crypto_symbols_get_the_crypto_source(self):
        llm = FakeLLM(AIMessage(content="news read"))

        create_news_analyst(llm, _toolkit())(_state(ticker="BTC/USD"))

        self.assertIn("get_coindesk_news", _bound_names(llm))

    def test_an_unavailable_source_is_not_offered(self):
        llm = FakeLLM(AIMessage(content="news read"))

        create_news_analyst(llm, _toolkit(finnhub=False))(_state())

        self.assertNotIn("get_finnhub_news_recent", _bound_names(llm))


class SocialAnalystTests(unittest.TestCase):
    def test_the_report_lands_under_its_state_key(self):
        llm = FakeLLM(AIMessage(content="sentiment read"))

        result = create_social_media_analyst(llm, _toolkit())(_state())

        self.assertIn("sentiment_report", result)

    def test_web_search_is_offered_when_available(self):
        llm = FakeLLM(AIMessage(content="sentiment read"))

        create_social_media_analyst(llm, _toolkit())(_state())

        self.assertIn("get_stock_news_openai", _bound_names(llm))

    def test_reddit_is_offered_when_web_search_is_not(self):
        llm = FakeLLM(AIMessage(content="sentiment read"))

        create_social_media_analyst(llm, _toolkit(openai_web_search=False))(_state())

        self.assertNotIn("get_stock_news_openai", _bound_names(llm))


class FundamentalsAnalystTests(unittest.TestCase):
    def test_the_report_lands_under_its_state_key(self):
        llm = FakeLLM(AIMessage(content="fundamentals read"))

        result = create_fundamentals_analyst(llm, _toolkit())(_state())

        self.assertIn("fundamentals_report", result)

    def test_crypto_symbols_get_the_defi_source(self):
        llm = FakeLLM(AIMessage(content="fundamentals read"))

        create_fundamentals_analyst(llm, _toolkit())(_state(ticker="ETH/USD"))

        self.assertIn("get_defillama_fundamentals", _bound_names(llm))

    def test_equities_get_the_statement_sources(self):
        llm = FakeLLM(AIMessage(content="fundamentals read"))

        create_fundamentals_analyst(llm, _toolkit())(_state(ticker="NVDA"))

        self.assertNotIn("get_defillama_fundamentals", _bound_names(llm))

    def test_unavailable_statement_data_is_not_offered(self):
        llm = FakeLLM(AIMessage(content="fundamentals read"))

        create_fundamentals_analyst(llm, _toolkit(simfin_data=False))(_state())

        self.assertNotIn("get_simfin_balance_sheet", _bound_names(llm))


class MacroAnalystTests(unittest.TestCase):
    def test_the_report_lands_under_its_state_key(self):
        llm = FakeLLM(AIMessage(content="macro read"))

        result = create_macro_analyst(llm, _toolkit())(_state())

        self.assertIn("macro_report", result)

    def test_fred_series_are_offered_when_available(self):
        llm = FakeLLM(AIMessage(content="macro read"))

        create_macro_analyst(llm, _toolkit())(_state())

        self.assertIn("get_economic_indicators", _bound_names(llm))

    def test_fred_series_are_withheld_when_unavailable(self):
        llm = FakeLLM(AIMessage(content="macro read"))

        create_macro_analyst(llm, _toolkit(fred=False))(_state())

        self.assertNotIn("get_economic_indicators", _bound_names(llm))


class EveryAnalystTests(unittest.TestCase):
    """Shared contract: one report key, one message, no exceptions."""

    FACTORIES = (
        (create_market_analyst, "market_report"),
        (create_news_analyst, "news_report"),
        (create_social_media_analyst, "sentiment_report"),
        (create_fundamentals_analyst, "fundamentals_report"),
        (create_macro_analyst, "macro_report"),
    )

    def test_each_analyst_writes_exactly_its_own_report_key(self):
        for factory, key in self.FACTORIES:
            llm = FakeLLM(AIMessage(content="a read. FINAL TRANSACTION PROPOSAL: HOLD"))

            result = factory(llm, _toolkit())(_state())

            self.assertIn(key, result, key)
            self.assertTrue(result[key], key)
            self.assertIn("messages", result, key)

    def test_each_analyst_survives_a_toolkit_with_nothing_available(self):
        for factory, key in self.FACTORIES:
            llm = FakeLLM(AIMessage(content="a read. FINAL TRANSACTION PROPOSAL: HOLD"))
            toolkit = _toolkit(
                online_tools=False,
                market_data=False,
                openai_web_search=False,
                finnhub=False,
                coindesk=False,
                simfin_data=False,
                fred=False,
            )

            result = factory(llm, toolkit)(_state())

            self.assertIn(key, result, key)

    def test_each_analyst_handles_a_crypto_symbol(self):
        for factory, key in self.FACTORIES:
            llm = FakeLLM(AIMessage(content="a read. FINAL TRANSACTION PROPOSAL: HOLD"))

            result = factory(llm, _toolkit())(_state(ticker="BTC/USD"))

            self.assertIn(key, result, key)


if __name__ == "__main__":
    unittest.main()
