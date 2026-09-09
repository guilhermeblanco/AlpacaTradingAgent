"""Tests for the Reddit surfaces and the thin tool wrappers.

Reddit is served from a downloaded dataset when one is present and from the
live API otherwise; which one answered is stamped into the report so an
analyst can weigh it. The remaining wrappers are one-line adapters, and the
thing worth pinning about them is that a failure in what they wrap comes
back as text rather than as an exception inside a graph node.
"""

from __future__ import annotations

import unittest
from unittest import mock

from tradingagents.dataflows import interface


def _post(title="A post", content="", subreddit=None):
    post = {"title": title, "content": content}
    if subreddit:
        post["subreddit"] = subreddit
    return post


class RedditFixture(unittest.TestCase):
    def _run(self, fetcher, *args, local=None, online=None, online_tools=True):
        calls = {"local": [], "online": []}

        def local_fetch(*a, **k):
            calls["local"].append((a, k))
            if isinstance(local, Exception):
                raise local
            return list(local or [])

        def online_fetch(**k):
            calls["online"].append(k)
            return list(online or [])

        with mock.patch.object(
            interface, "get_config", lambda: {"online_tools": online_tools}
        ):
            with mock.patch.object(interface, "fetch_top_from_category", local_fetch):
                with mock.patch.object(
                    interface, "fetch_top_from_category_online", online_fetch
                ):
                    return fetcher(*args), calls


class RedditGlobalNewsTests(RedditFixture):
    def _news(self, **kwargs):
        return self._run(
            interface.get_reddit_global_news, "2026-09-09", 2, 5, **kwargs
        )

    def test_local_posts_are_rendered_and_the_source_named(self):
        report, calls = self._news(local=[_post("Rates hold steady", "Body text.")])

        self.assertIn("### Rates hold steady", report)
        self.assertIn("Body text.", report)
        self.assertIn("source: reddit_local_dataset", report)
        self.assertEqual(calls["online"], [])

    def test_the_window_is_walked_one_day_at_a_time(self):
        _report, calls = self._news(local=[])

        self.assertEqual(len(calls["local"]), 3)

    def test_the_window_appears_in_the_heading(self):
        report, _calls = self._news(local=[])

        self.assertIn("from 2026-09-07 to 2026-09-09", report)

    def test_a_titleless_post_still_renders(self):
        report, _calls = self._news(local=[{"content": "orphan body"}])

        self.assertIn("### Untitled", report)

    def test_the_subreddit_is_shown_when_known(self):
        report, _calls = self._news(local=[_post(subreddit="investing")])

        self.assertIn("[r/investing]", report)

    def test_a_missing_local_dataset_falls_back_to_the_live_api(self):
        report, calls = self._news(
            local=FileNotFoundError("no dataset"), online=[_post("Live post")]
        )

        self.assertIn("### Live post", report)
        self.assertIn("source: reddit_live_api", report)
        self.assertEqual(len(calls["online"]), 1)

    def test_the_live_budget_covers_the_whole_window(self):
        _report, calls = self._news(
            local=NotADirectoryError("bad path"), online=[_post()]
        )

        self.assertEqual(calls["online"][0]["max_limit"], 15)

    def test_offline_runs_do_not_reach_the_live_api(self):
        report, calls = self._news(
            local=FileNotFoundError("no dataset"),
            online=[_post("Live post")],
            online_tools=False,
        )

        self.assertEqual(calls["online"], [])
        self.assertIn("No posts found", report)

    def test_no_posts_anywhere_says_so(self):
        report, _calls = self._news(local=[], online=[])

        self.assertIn("No posts found", report)


class RedditCompanyNewsTests(RedditFixture):
    def _news(self, **kwargs):
        return self._run(
            interface.get_reddit_company_news, "NVDA", "2026-09-09", 2, 5, **kwargs
        )

    def test_local_posts_are_rendered_and_the_source_named(self):
        report, calls = self._news(local=[_post("NVDA thread", "Body text.")])

        self.assertIn("### NVDA thread", report)
        self.assertIn("source: reddit_local_dataset", report)
        self.assertEqual(calls["online"], [])

    def test_the_ticker_is_passed_to_the_local_lookup(self):
        _report, calls = self._news(local=[])

        self.assertIn("NVDA", calls["local"][0][0])

    def test_the_search_terms_tried_are_reported(self):
        """Otherwise "no posts" is indistinguishable from "wrong search"."""
        report, _calls = self._news(local=[])

        self.assertIn("Tried search terms:", report)

    def test_the_search_terms_are_reported_alongside_results_too(self):
        report, _calls = self._news(local=[_post("NVDA thread")])

        self.assertIn("Searched terms:", report)

    def test_a_missing_local_dataset_falls_back_to_the_live_api(self):
        report, calls = self._news(
            local=ValueError("bad dataset"), online=[_post("Live post")]
        )

        self.assertIn("source: reddit_live_api", report)
        self.assertEqual(calls["online"][0]["query"], "NVDA")

    def test_offline_runs_do_not_reach_the_live_api(self):
        report, calls = self._news(
            local=FileNotFoundError("no dataset"), online=[_post()], online_tools=False
        )

        self.assertEqual(calls["online"], [])
        self.assertIn("No posts found", report)


class DefiLlamaWrapperTests(unittest.TestCase):
    def _fundamentals(self, ticker, result="the report", lookback=30):
        captured = {}

        def util(symbol, days):
            captured["symbol"] = symbol
            captured["days"] = days
            if isinstance(result, Exception):
                raise result
            return result

        with mock.patch.object(interface, "get_defillama_fundamentals_util", util):
            answer = interface.get_defillama_fundamentals(ticker, lookback)
        return answer, captured

    def test_the_report_is_passed_through(self):
        answer, _captured = self._fundamentals("UNI")

        self.assertEqual(answer, "the report")

    def test_a_quote_currency_is_stripped(self):
        for ticker in ("BTC/USD", "btcusd", "BTCUSDT"):
            _answer, captured = self._fundamentals(ticker)

            self.assertEqual(captured["symbol"], "BTC", ticker)

    def test_the_lookback_is_forwarded(self):
        _answer, captured = self._fundamentals("UNI", lookback=90)

        self.assertEqual(captured["days"], 90)

    def test_a_failure_is_reported_as_text(self):
        answer, _captured = self._fundamentals("UNI", result=RuntimeError("503"))

        self.assertIn("Error fetching DeFi Llama data for UNI", answer)
        self.assertIn("503", answer)


class TechnicalBriefTests(unittest.TestCase):
    def test_the_brief_is_returned_as_json(self):
        brief = mock.Mock()
        brief.model_dump_json.return_value = '{"symbol": "NVDA"}'

        with mock.patch(
            "tradingagents.dataflows.technical_brief.build_technical_brief",
            lambda *a: brief,
        ):
            answer = interface.get_technical_brief("NVDA", "2026-09-09")

        self.assertEqual(answer, '{"symbol": "NVDA"}')

    def test_a_failure_comes_back_as_json_too(self):
        """The caller parses this; prose would break it."""
        import json

        with mock.patch(
            "tradingagents.dataflows.technical_brief.build_technical_brief",
            mock.Mock(side_effect=RuntimeError("no bars")),
        ):
            answer = interface.get_technical_brief("NVDA", "2026-09-09")

        parsed = json.loads(answer)
        self.assertIn("no bars", parsed["error"])
        self.assertEqual(parsed["symbol"], "NVDA")


class ThinWrapperTests(unittest.TestCase):
    """Each of these forwards to a dataflow module with no logic of its own."""

    CASES = (
        (
            interface.get_earnings_calendar,
            "get_earnings_calendar_data",
            ("NVDA", "2026-01-01", "2026-03-31"),
        ),
        (
            interface.get_earnings_surprise_analysis,
            "get_earnings_surprises_analysis",
            ("NVDA", "2026-09-09"),
        ),
        (interface.get_macro_analysis, "get_macro_economic_summary", ("2026-09-09",)),
        (
            interface.get_economic_indicators,
            "get_economic_indicators_report",
            ("2026-09-09",),
        ),
        (
            interface.get_yield_curve_analysis,
            "get_treasury_yield_curve",
            ("2026-09-09",),
        ),
    )

    def test_each_wrapper_forwards_to_its_source(self):
        for wrapper, target, args in self.CASES:
            with mock.patch.object(interface, target, lambda *a, **k: "the answer"):
                self.assertEqual(wrapper(*args), "the answer", target)

    def test_the_alpaca_aliases_delegate_to_the_neutral_tools(self):
        with mock.patch.object(interface, "get_market_data", lambda *a, **k: "window"):
            self.assertEqual(interface.get_alpaca_data("NVDA", "2026-09-09"), "window")

        with mock.patch.object(
            interface, "get_market_data_window", lambda *a, **k: "window"
        ):
            self.assertEqual(
                interface.get_alpaca_data_window("NVDA", "2026-09-09"), "window"
            )


if __name__ == "__main__":
    unittest.main()
