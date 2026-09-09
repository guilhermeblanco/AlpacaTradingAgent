"""Tests for the Google News fetcher.

The RSS feed is the primary source and the HTML scrape is a fallback for
when it comes back empty. Both paths have to survive Google changing its
markup or throttling the caller, because the news analyst has no other way
to see a headline.
"""

from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

from tradingagents.dataflows import googlenews_utils
from tradingagents.dataflows.googlenews_utils import (
    _getNewsDataRSS,
    _getNewsDataScrape,
    getNewsData,
    is_rate_limited,
)


def _entry(title, *, published="Wed, 14 Jan 2026 12:00:00 GMT", summary=None, link="https://x"):
    entry = SimpleNamespace(title=title, link=link, published=published)
    if summary is not None:
        entry.summary = summary
    return entry


def _feed(*entries):
    return SimpleNamespace(entries=list(entries))


class RateLimitTests(unittest.TestCase):
    def test_a_429_is_recognized(self):
        self.assertTrue(is_rate_limited(SimpleNamespace(status_code=429)))

    def test_other_statuses_are_not(self):
        for status in (200, 404, 500):
            self.assertFalse(is_rate_limited(SimpleNamespace(status_code=status)))


class RssTests(unittest.TestCase):
    def _parse(self, feed, *, start="2026-01-01", end="2026-01-31", **kwargs):
        captured = {}

        def parse(url):
            captured["url"] = url
            if isinstance(feed, Exception):
                raise feed
            return feed

        with mock.patch.object(googlenews_utils, "feedparser", SimpleNamespace(parse=parse)):
            results = _getNewsDataRSS("NVDA earnings", start, end, **kwargs)
        return results, captured

    def test_a_headline_is_returned_with_its_source_split_out(self):
        """Google News appends " - Publisher" to every RSS title."""
        results, _ = self._parse(_feed(_entry("NVDA beats estimates - Reuters")))

        self.assertEqual(results[0]["title"], "NVDA beats estimates")
        self.assertEqual(results[0]["source"], "Reuters")

    def test_only_the_last_dash_separates_the_source(self):
        results, _ = self._parse(_feed(_entry("AI chips - and margins - Reuters")))

        self.assertEqual(results[0]["title"], "AI chips - and margins")
        self.assertEqual(results[0]["source"], "Reuters")

    def test_a_title_without_a_source_suffix_keeps_its_whole_title(self):
        results, _ = self._parse(_feed(_entry("NVDA beats estimates")))

        self.assertEqual(results[0]["title"], "NVDA beats estimates")
        self.assertEqual(results[0]["source"], "Unknown")

    def test_the_title_stands_in_for_a_missing_snippet(self):
        results, _ = self._parse(_feed(_entry("NVDA beats - Reuters")))

        self.assertEqual(results[0]["snippet"], "NVDA beats")

    def test_html_is_stripped_out_of_a_snippet(self):
        results, _ = self._parse(
            _feed(_entry("NVDA beats - Reuters", summary='<a href="x">Full story</a>'))
        )

        self.assertEqual(results[0]["snippet"], "Full story")

    def test_the_date_range_is_pushed_into_the_query(self):
        _results, captured = self._parse(_feed())

        self.assertIn("after%3A2026-01-01", captured["url"])

    def test_the_exclusive_before_bound_is_advanced_by_a_day(self):
        """Otherwise the requested end date is never returned."""
        _results, captured = self._parse(_feed())

        self.assertIn("before%3A2026-02-01", captured["url"])

    def test_a_slash_formatted_date_is_accepted(self):
        _results, captured = self._parse(
            _feed(), start="01/01/2026", end="01/31/2026"
        )

        self.assertIn("after%3A2026-01-01", captured["url"])

    def test_a_plus_encoded_query_is_decoded_before_re_encoding(self):
        captured = {}

        def parse(url):
            captured["url"] = url
            return _feed()

        with mock.patch.object(
            googlenews_utils, "feedparser", SimpleNamespace(parse=parse)
        ):
            _getNewsDataRSS("NVDA+earnings", "2026-01-01", "2026-01-31")

        self.assertIn("NVDA+earnings", captured["url"])
        self.assertNotIn("%2B", captured["url"])

    def test_an_article_outside_the_window_is_dropped(self):
        results, _ = self._parse(
            _feed(_entry("stale - Reuters", published="Wed, 14 Jan 2020 12:00:00 GMT"))
        )

        self.assertEqual(results, [])

    def test_an_undated_article_is_kept_for_a_window_reaching_the_present(self):
        today = datetime.now().strftime("%Y-%m-%d")

        results, _ = self._parse(
            _feed(_entry("undated - Reuters", published="whenever")),
            start="2026-01-01",
            end=today,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["date"], "whenever")

    def test_an_undated_article_is_dropped_from_a_historical_window(self):
        """Its availability on the requested date cannot be proven."""
        results, _ = self._parse(
            _feed(_entry("undated - Reuters", published="whenever")),
            start="2020-01-01",
            end="2020-01-31",
        )

        self.assertEqual(results, [])

    def test_the_result_count_is_capped(self):
        entries = [_entry(f"headline {i} - Reuters") for i in range(10)]

        results, _ = self._parse(_feed(*entries), max_results=3)

        self.assertEqual(len(results), 3)

    def test_a_parse_failure_yields_nothing_rather_than_raising(self):
        results, _ = self._parse(RuntimeError("feed unreachable"))

        self.assertEqual(results, [])

    def test_without_feedparser_installed_rss_yields_nothing(self):
        with mock.patch.object(googlenews_utils, "feedparser", None):
            self.assertEqual(
                _getNewsDataRSS("NVDA", "2026-01-01", "2026-01-31"), []
            )


RESULT_HTML = """
<div class="SoaBEf">
  <a href="https://news.example/article"></a>
  <div class="MBeuO">NVDA beats estimates</div>
  <div class="GI74Re">Revenue up 40%.</div>
  <div class="LfVVr">2 days ago</div>
  <div class="NUnG9d"><span>Reuters</span></div>
</div>
"""


class ScrapeTests(unittest.TestCase):
    def _scrape(self, pages, *, start="2026-01-01", end="2026-01-31", max_pages=3):
        served = list(pages)
        captured = []

        def make_request(url, headers):
            captured.append(url)
            body = served.pop(0) if served else ""
            if isinstance(body, Exception):
                raise body
            return SimpleNamespace(content=body.encode("utf-8"))

        with mock.patch.object(googlenews_utils, "make_request", make_request):
            results = _getNewsDataScrape("NVDA", start, end, max_pages=max_pages)
        return results, captured

    def test_a_result_card_is_parsed(self):
        results, _ = self._scrape([RESULT_HTML])

        self.assertEqual(results[0]["title"], "NVDA beats estimates")
        self.assertEqual(results[0]["snippet"], "Revenue up 40%.")
        self.assertEqual(results[0]["source"], "Reuters")
        self.assertEqual(results[0]["link"], "https://news.example/article")

    def test_dates_are_converted_to_the_format_google_expects(self):
        _results, captured = self._scrape([""])

        self.assertIn("cd_min:01/01/2026", captured[0])
        self.assertIn("cd_max:01/31/2026", captured[0])

    def test_an_already_slash_formatted_date_is_left_alone(self):
        _results, captured = self._scrape([""], start="01/01/2026", end="01/31/2026")

        self.assertIn("cd_min:01/01/2026", captured[0])

    def test_an_alternate_card_class_is_recognized(self):
        """Google renames these classes regularly."""
        html = RESULT_HTML.replace("SoaBEf", "xuvV6b")

        results, _ = self._scrape([html])

        self.assertEqual(results[0]["title"], "NVDA beats estimates")

    def test_a_card_without_a_title_is_skipped(self):
        html = RESULT_HTML.replace('<div class="MBeuO">NVDA beats estimates</div>', "")

        results, _ = self._scrape([html])

        self.assertEqual(results, [])

    def test_missing_optional_fields_get_placeholders(self):
        html = """
        <div class="SoaBEf">
          <div class="MBeuO">Bare headline</div>
        </div>
        """

        results, _ = self._scrape([html])

        self.assertEqual(results[0]["snippet"], "Bare headline")
        self.assertEqual(results[0]["date"], "Unknown")
        self.assertEqual(results[0]["source"], "Unknown")
        self.assertEqual(results[0]["link"], "")

    def test_paging_stops_when_there_is_no_next_link(self):
        _results, captured = self._scrape([RESULT_HTML, RESULT_HTML])

        self.assertEqual(len(captured), 1)

    def test_paging_follows_the_next_link(self):
        with_next = RESULT_HTML + '<a id="pnnext" href="/next"></a>'

        results, captured = self._scrape([with_next, RESULT_HTML])

        self.assertEqual(len(captured), 2)
        self.assertEqual(len(results), 2)
        self.assertIn("start=10", captured[1])

    def test_paging_respects_the_page_cap(self):
        with_next = RESULT_HTML + '<a id="pnnext" href="/next"></a>'

        _results, captured = self._scrape([with_next] * 5, max_pages=2)

        self.assertEqual(len(captured), 2)

    def test_an_empty_page_ends_the_scrape(self):
        results, captured = self._scrape(["<html></html>", RESULT_HTML])

        self.assertEqual(results, [])
        self.assertEqual(len(captured), 1)

    def test_a_failed_request_ends_the_scrape_without_raising(self):
        results, _ = self._scrape([RuntimeError("blocked after retries")])

        self.assertEqual(results, [])


class FallbackTests(unittest.TestCase):
    def test_rss_results_are_used_and_the_scrape_is_not_attempted(self):
        rss = [{"title": "from rss"}]

        with mock.patch.object(googlenews_utils, "_getNewsDataRSS", lambda *a, **k: rss):
            with mock.patch.object(googlenews_utils, "_getNewsDataScrape") as scrape:
                self.assertEqual(getNewsData("NVDA", "2026-01-01", "2026-01-31"), rss)

        scrape.assert_not_called()

    def test_an_empty_rss_feed_falls_back_to_scraping(self):
        scraped = [{"title": "from scrape"}]

        with mock.patch.object(googlenews_utils, "_getNewsDataRSS", lambda *a, **k: []):
            with mock.patch.object(
                googlenews_utils, "_getNewsDataScrape", lambda *a, **k: scraped
            ):
                self.assertEqual(
                    getNewsData("NVDA", "2026-01-01", "2026-01-31"), scraped
                )

    def test_the_page_budget_is_translated_into_an_rss_result_cap(self):
        captured = {}

        def rss(query, start, end, max_results=20):
            captured["max_results"] = max_results
            return [{"title": "x"}]

        with mock.patch.object(googlenews_utils, "_getNewsDataRSS", rss):
            getNewsData("NVDA", "2026-01-01", "2026-01-31", max_pages=4)

        self.assertEqual(captured["max_results"], 40)


class RequestTests(unittest.TestCase):
    def test_the_request_is_bounded_by_a_timeout(self):
        captured = {}

        def get(url, headers=None, timeout=None):
            captured["timeout"] = timeout
            return SimpleNamespace(status_code=200)

        with mock.patch.object(googlenews_utils.time, "sleep"):
            with mock.patch.object(googlenews_utils.requests, "get", get):
                googlenews_utils.make_request("https://x", {})

        self.assertEqual(captured["timeout"], 15)


if __name__ == "__main__":
    unittest.main()
