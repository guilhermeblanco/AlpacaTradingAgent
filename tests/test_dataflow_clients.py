"""Tests for the thin vendor clients under dataflows.

Each of these turns one HTTP endpoint into something an analyst prompt can
read. What matters is that a missing key, an error payload, or an
unexpected shape produces a usable answer instead of a traceback that
takes an analyst node down with it.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import requests

from tradingagents.dataflows import coindesk_utils, finnhub_utils


def _response(payload, *, status=200):
    def raise_for_status():
        if status >= 400:
            raise requests.exceptions.HTTPError(f"{status}")

    return SimpleNamespace(
        status_code=status,
        json=lambda: payload,
        raise_for_status=raise_for_status,
    )


class FinnhubRequestTests(unittest.TestCase):
    def _get(self, payload, *, key="fh-key", status=200):
        captured = {}

        def get(url, params=None, timeout=None):
            captured["url"] = url
            captured["params"] = params
            captured["timeout"] = timeout
            return _response(payload, status=status)

        with mock.patch.object(finnhub_utils, "get_finnhub_api_key", lambda: key):
            with mock.patch.object(finnhub_utils.requests, "get", get):
                yield_value = finnhub_utils._request_finnhub_json("/company-news", {"symbol": "NVDA"})
        return yield_value, captured

    def test_the_api_key_travels_as_a_token_parameter(self):
        _data, captured = self._get([])

        self.assertEqual(captured["params"]["token"], "fh-key")
        self.assertEqual(captured["params"]["symbol"], "NVDA")

    def test_the_request_is_bounded_by_a_timeout(self):
        """An unbounded call would hang the analyst node that made it."""
        _data, captured = self._get([])

        self.assertEqual(captured["timeout"], finnhub_utils._FINNHUB_TIMEOUT_SECONDS)

    def test_the_endpoint_is_appended_to_the_base_url(self):
        _data, captured = self._get([])

        self.assertEqual(captured["url"], f"{finnhub_utils._FINNHUB_BASE_URL}/company-news")

    def test_a_missing_key_is_refused_before_the_request(self):
        with mock.patch.object(finnhub_utils, "get_finnhub_api_key", lambda: ""):
            with mock.patch.object(finnhub_utils.requests, "get") as get:
                with self.assertRaises(ValueError):
                    finnhub_utils._request_finnhub_json("/company-news", {})

        get.assert_not_called()

    def test_an_http_error_propagates(self):
        with self.assertRaises(requests.exceptions.HTTPError):
            self._get({}, status=500)

    def test_an_error_field_in_a_200_body_is_raised(self):
        """Finnhub reports quota exhaustion with a 200 and an error key."""
        with self.assertRaises(RuntimeError) as raised:
            self._get({"error": "API limit reached"})

        self.assertIn("API limit reached", str(raised.exception))


class FinnhubClientTests(unittest.TestCase):
    def test_a_missing_key_is_refused(self):
        with mock.patch.object(finnhub_utils, "get_finnhub_api_key", lambda: None):
            with self.assertRaises(ValueError):
                finnhub_utils.get_finnhub_client()


class CompanyNewsTests(unittest.TestCase):
    def _fetch(self, payload):
        with mock.patch.object(
            finnhub_utils, "_request_finnhub_json", lambda *a, **k: payload
        ):
            return finnhub_utils.fetch_company_news_live("nvda", "2026-01-01", "2026-01-31")

    def test_headlines_come_back_newest_first(self):
        rows = self._fetch(
            [
                {"headline": "older", "datetime": 100},
                {"headline": "newer", "datetime": 200},
            ]
        )

        self.assertEqual([row["headline"] for row in rows], ["newer", "older"])

    def test_an_entry_without_a_timestamp_sorts_last(self):
        rows = self._fetch([{"headline": "undated"}, {"headline": "dated", "datetime": 5}])

        self.assertEqual(rows[0]["headline"], "dated")

    def test_a_null_timestamp_does_not_break_sorting(self):
        rows = self._fetch([{"headline": "a", "datetime": None}, {"headline": "b", "datetime": 5}])

        self.assertEqual(rows[0]["headline"], "b")

    def test_non_dict_entries_are_dropped(self):
        self.assertEqual(self._fetch(["junk", None, {"datetime": 1}]), [{"datetime": 1}])

    def test_an_unexpected_shape_yields_nothing(self):
        self.assertEqual(self._fetch({"data": []}), [])

    def test_the_symbol_is_upper_cased_for_the_vendor(self):
        captured = {}

        def request(path, params):
            captured.update(params)
            return []

        with mock.patch.object(finnhub_utils, "_request_finnhub_json", request):
            finnhub_utils.fetch_company_news_live("nvda", "2026-01-01", "2026-01-31")

        self.assertEqual(captured["symbol"], "NVDA")
        self.assertEqual(captured["from"], "2026-01-01")
        self.assertEqual(captured["to"], "2026-01-31")


class InsiderSentimentTests(unittest.TestCase):
    def _fetch(self, payload):
        with mock.patch.object(
            finnhub_utils, "_request_finnhub_json", lambda *a, **k: payload
        ):
            return finnhub_utils.fetch_insider_sentiment_live("NVDA", "2026-01-01", "2026-01-31")

    def test_the_rows_are_unwrapped_from_the_data_envelope(self):
        self.assertEqual(self._fetch({"data": [{"mspr": 12.0}]}), [{"mspr": 12.0}])

    def test_a_missing_envelope_yields_nothing(self):
        self.assertEqual(self._fetch({}), [])

    def test_a_non_dict_payload_yields_nothing(self):
        self.assertEqual(self._fetch([{"mspr": 1.0}]), [])

    def test_non_dict_rows_are_dropped(self):
        self.assertEqual(self._fetch({"data": ["junk", {"mspr": 1.0}]}), [{"mspr": 1.0}])


class InsiderTransactionTests(unittest.TestCase):
    def _fetch(self, payload):
        with mock.patch.object(
            finnhub_utils, "_request_finnhub_json", lambda *a, **k: payload
        ):
            return finnhub_utils.fetch_insider_transactions_live(
                "NVDA", "2026-01-01", "2026-01-31"
            )

    def test_filings_come_back_newest_first(self):
        rows = self._fetch(
            {
                "data": [
                    {"name": "older", "filingDate": "2026-01-02"},
                    {"name": "newer", "filingDate": "2026-01-20"},
                ]
            }
        )

        self.assertEqual([row["name"] for row in rows], ["newer", "older"])

    def test_a_missing_filing_date_falls_back_to_the_transaction_date(self):
        rows = self._fetch(
            {
                "data": [
                    {"name": "no-filing", "transactionDate": 1_767_225_600},
                    {"name": "filed", "filingDate": "2020-01-01"},
                ]
            }
        )

        self.assertEqual(rows[0]["name"], "no-filing")

    def test_a_missing_envelope_yields_nothing(self):
        self.assertEqual(self._fetch({"other": 1}), [])


class TimestampTests(unittest.TestCase):
    def test_an_epoch_second_becomes_a_utc_date(self):
        self.assertEqual(
            finnhub_utils._timestamp_to_date_str(1_767_225_600), "2026-01-01"
        )

    def test_a_non_numeric_value_yields_an_empty_string(self):
        self.assertEqual(finnhub_utils._timestamp_to_date_str("2026-01-01"), "")
        self.assertEqual(finnhub_utils._timestamp_to_date_str(None), "")


class CachedFinnhubDataTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)

    def _write(self, data_type, payload, *, ticker="NVDA", period=None):
        folder = self.data_dir / "finnhub_data" / data_type
        folder.mkdir(parents=True, exist_ok=True)
        suffix = f"_{period}" if period else ""
        (folder / f"{ticker}{suffix}_data_formatted.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def test_only_dates_inside_the_range_are_returned(self):
        self._write(
            "news_data",
            {
                "2026-01-01": [{"headline": "before"}],
                "2026-01-15": [{"headline": "inside"}],
                "2026-02-01": [{"headline": "after"}],
            },
        )

        found = finnhub_utils.get_data_in_range(
            "NVDA", "2026-01-10", "2026-01-31", "news_data", str(self.data_dir)
        )

        self.assertEqual(list(found), ["2026-01-15"])

    def test_the_bounds_are_inclusive(self):
        self._write("news_data", {"2026-01-10": [1], "2026-01-31": [1]})

        found = finnhub_utils.get_data_in_range(
            "NVDA", "2026-01-10", "2026-01-31", "news_data", str(self.data_dir)
        )

        self.assertEqual(sorted(found), ["2026-01-10", "2026-01-31"])

    def test_days_with_no_entries_are_dropped(self):
        self._write("news_data", {"2026-01-15": []})

        found = finnhub_utils.get_data_in_range(
            "NVDA", "2026-01-01", "2026-01-31", "news_data", str(self.data_dir)
        )

        self.assertEqual(found, {})

    def test_a_period_selects_the_annual_or_quarterly_file(self):
        self._write("fin_as_reported", {"2026-01-15": [1]}, period="annual")

        found = finnhub_utils.get_data_in_range(
            "NVDA",
            "2026-01-01",
            "2026-01-31",
            "fin_as_reported",
            str(self.data_dir),
            period="annual",
        )

        self.assertEqual(list(found), ["2026-01-15"])

    def test_a_missing_file_is_reported_as_such(self):
        with self.assertRaises(FileNotFoundError):
            finnhub_utils.get_data_in_range(
                "MISSING", "2026-01-01", "2026-01-31", "news_data", str(self.data_dir)
            )


class CoindeskNewsTests(unittest.TestCase):
    ARTICLE = {
        "title": "BTC rallies",
        "source_info": {"name": "CoinDesk"},
        "body": "First sentence. Second sentence. Third sentence.",
        "published_on": 1_767_225_600,
    }

    def _get_news(self, payload=None, *, key="cd-key", error=None, n=5):
        def get(url, headers=None):
            if error:
                raise error
            return _response(payload)

        with mock.patch.object(coindesk_utils, "get_api_key", lambda *a: key):
            with mock.patch.object(coindesk_utils.requests, "get", get):
                return coindesk_utils.get_news("BTC", n)

    def test_articles_are_rendered_as_markdown_sections(self):
        rendered = self._get_news({"Type": 100, "Data": [self.ARTICLE]})

        self.assertIn("### BTC rallies", rendered)
        self.assertIn("CoinDesk", rendered)
        self.assertIn("2026-01-01 00:00:00 UTC", rendered)

    def test_only_the_first_n_sentences_of_the_body_are_kept(self):
        rendered = self._get_news({"Type": 100, "Data": [self.ARTICLE]}, n=2)

        self.assertIn("Second sentence.", rendered)
        self.assertNotIn("Third sentence.", rendered)

    def test_the_publication_time_is_rendered_in_utc(self):
        """Otherwise the same article carries a different date depending on
        which machine the run happened on."""
        rendered = self._get_news({"Type": 100, "Data": [self.ARTICLE]})

        self.assertIn("published: 2026-01-01 00:00:00 UTC", rendered)

    def test_missing_fields_get_readable_placeholders(self):
        rendered = self._get_news({"Type": 100, "Data": [{"published_on": 0}]})

        self.assertIn("No Title", rendered)
        self.assertIn("Unknown Source", rendered)

    def test_a_missing_key_is_reported_rather_than_raised(self):
        """The caller is an analyst prompt, so the answer has to be text."""
        with mock.patch.object(coindesk_utils, "get_api_key", lambda *a: None):
            answer = coindesk_utils.get_news("BTC")

        self.assertIn("COINDESK_API_KEY", answer)

    def test_an_unsuccessful_response_type_reads_as_no_news(self):
        self.assertIn("No news found", self._get_news({"Type": 500, "Data": []}))

    def test_an_empty_data_list_reads_as_no_news(self):
        self.assertIn("No news found", self._get_news({"Type": 100, "Data": []}))

    def test_a_network_failure_is_reported_as_text(self):
        answer = self._get_news(error=requests.exceptions.ConnectionError("offline"))

        self.assertIn("Error fetching news", answer)

    def test_an_unexpected_failure_is_reported_as_text(self):
        answer = self._get_news(error=RuntimeError("boom"))

        self.assertIn("An error occurred", answer)


if __name__ == "__main__":
    unittest.main()
