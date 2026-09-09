"""Tests for the cached-then-live dataflow surfaces.

Finnhub news and insider filings are served from an on-disk cache when the
run is backtesting a past date, and from the live API otherwise. The two
paths render the same markdown and dedupe against each other, so the
report an analyst reads should not depend on which one answered.

SimFin statements are cache-only: the files are a large optional download,
so their absence has to read as "unavailable", never as a traceback.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from tradingagents.dataflows import interface


class DataflowFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)
        patcher = mock.patch.object(interface, "DATA_DIR", str(self.data_dir))
        patcher.start()
        self.addCleanup(patcher.stop)
        self._online(True)

    def _online(self, enabled):
        patcher = mock.patch.object(
            interface, "get_config", lambda: {"online_tools": enabled}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _cache(self, data_type, payload, *, ticker="NVDA"):
        import json

        folder = self.data_dir / "finnhub_data" / data_type
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{ticker}_data_formatted.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def _live(self, name, value):
        patcher = mock.patch.object(
            interface,
            name,
            mock.Mock(side_effect=value) if isinstance(value, Exception)
            else mock.Mock(return_value=value),
        )
        stub = patcher.start()
        self.addCleanup(patcher.stop)
        return stub


class FinnhubNewsTests(DataflowFixture):
    def test_cached_headlines_are_rendered_and_labelled(self):
        self._cache(
            "news_data",
            {"2026-09-05": [{"headline": "Beat", "summary": "Strong quarter."}]},
        )
        live = self._live("fetch_company_news_live", [])

        report = interface.get_finnhub_news("NVDA", "2026-09-09", 7)

        self.assertIn("### Beat (2026-09-05)", report)
        self.assertIn("Strong quarter.", report)
        self.assertIn("source: cache", report)
        live.assert_not_called()

    def test_the_lookback_window_appears_in_the_heading(self):
        self._live("fetch_company_news_live", [])

        report = interface.get_finnhub_news("NVDA", "2026-09-09", 7)

        self.assertIn("from 2026-09-02 to 2026-09-09", report)

    def test_an_empty_cache_falls_through_to_the_live_api(self):
        self._live(
            "fetch_company_news_live",
            [{"headline": "Live", "summary": "s", "datetime": 1_767_225_600}],
        )

        report = interface.get_finnhub_news("NVDA", "2026-09-09", 7)

        self.assertIn("### Live (2026-01-01)", report)
        self.assertIn("source: finnhub_live_api", report)

    def test_a_live_entry_without_a_timestamp_uses_its_date_field(self):
        self._live("fetch_company_news_live", [{"headline": "Live", "date": "2026-09-08"}])

        self.assertIn("(2026-09-08)", interface.get_finnhub_news("NVDA", "2026-09-09", 7))

    def test_a_live_entry_with_neither_falls_back_to_the_run_date(self):
        self._live("fetch_company_news_live", [{"headline": "Live"}])

        self.assertIn("(2026-09-09)", interface.get_finnhub_news("NVDA", "2026-09-09", 7))

    def test_a_live_entry_without_a_summary_falls_back_to_its_link(self):
        self._live(
            "fetch_company_news_live", [{"headline": "Live", "url": "https://x/y"}]
        )

        self.assertIn("https://x/y", interface.get_finnhub_news("NVDA", "2026-09-09", 7))

    def test_offline_runs_do_not_reach_the_live_api(self):
        """A backtest must not read headlines published after its date."""
        self._online(False)
        live = self._live("fetch_company_news_live", [{"headline": "Live"}])

        report = interface.get_finnhub_news("NVDA", "2026-09-09", 7)

        live.assert_not_called()
        self.assertIn("No Finnhub news items found", report)

    def test_a_live_failure_reads_as_no_news(self):
        self._live("fetch_company_news_live", RuntimeError("quota"))

        self.assertIn(
            "No Finnhub news items found", interface.get_finnhub_news("NVDA", "2026-09-09", 7)
        )

    def test_empty_cache_days_are_skipped(self):
        self._cache("news_data", {"2026-09-05": []})
        self._live("fetch_company_news_live", [])

        self.assertIn(
            "No Finnhub news items found", interface.get_finnhub_news("NVDA", "2026-09-09", 7)
        )


class InsiderSentimentTests(DataflowFixture):
    def test_cached_records_are_rendered(self):
        self._cache(
            "insider_senti",
            {"2026-09-05": [{"year": 2026, "month": 8, "change": -1000, "mspr": -12.5}]},
        )
        live = self._live("fetch_insider_sentiment_live", [])

        report = interface.get_finnhub_company_insider_sentiment("NVDA", "2026-09-09", 15)

        self.assertIn("### 2026-8:", report)
        self.assertIn("Change: -1000", report)
        self.assertIn("Monthly Share Purchase Ratio: -12.5", report)
        self.assertIn("source: cache", report)
        live.assert_not_called()

    def test_a_record_repeated_across_cache_days_is_only_shown_once(self):
        """The cache buckets by fetch day, so a monthly figure recurs."""
        record = {"year": 2026, "month": 8, "change": -1000, "mspr": -12.5}
        self._cache("insider_senti", {"2026-09-05": [record], "2026-09-06": [record]})
        self._live("fetch_insider_sentiment_live", [])

        report = interface.get_finnhub_company_insider_sentiment("NVDA", "2026-09-09", 15)

        self.assertEqual(report.count("### 2026-8:"), 1)

    def test_an_empty_cache_falls_through_to_the_live_api(self):
        self._live(
            "fetch_insider_sentiment_live",
            [{"year": 2026, "month": 8, "change": 500, "mspr": 4.0}],
        )

        report = interface.get_finnhub_company_insider_sentiment("NVDA", "2026-09-09", 15)

        self.assertIn("source: finnhub_live_api", report)
        self.assertIn("Change: 500", report)

    def test_offline_runs_do_not_reach_the_live_api(self):
        self._online(False)
        live = self._live("fetch_insider_sentiment_live", [{"year": 2026}])

        report = interface.get_finnhub_company_insider_sentiment("NVDA", "2026-09-09", 15)

        live.assert_not_called()
        self.assertIn("No records found", report)

    def test_a_live_failure_reads_as_no_records(self):
        self._live("fetch_insider_sentiment_live", RuntimeError("quota"))

        self.assertIn(
            "No records found",
            interface.get_finnhub_company_insider_sentiment("NVDA", "2026-09-09", 15),
        )

    def test_the_field_meanings_are_explained_to_the_model(self):
        self._live(
            "fetch_insider_sentiment_live",
            [{"year": 2026, "month": 8, "change": 1, "mspr": 1.0}],
        )

        report = interface.get_finnhub_company_insider_sentiment("NVDA", "2026-09-09", 15)

        self.assertIn("net insider buying/selling", report)


class InsiderTransactionTests(DataflowFixture):
    FILING = {
        "filingDate": "2026-09-05",
        "name": "A Director",
        "change": -5000,
        "share": 10_000,
        "transactionPrice": 120.5,
        "transactionCode": "S",
        "transactionDate": "2026-09-03",
    }

    def test_cached_filings_are_rendered(self):
        self._cache("insider_trans", {"2026-09-05": [self.FILING]})
        live = self._live("fetch_insider_transactions_live", [])

        report = interface.get_finnhub_company_insider_transactions("NVDA", "2026-09-09", 15)

        self.assertIn("### Filing Date: 2026-09-05, A Director:", report)
        self.assertIn("Transaction Price: 120.5", report)
        self.assertIn("Transaction Code: S", report)
        self.assertIn("source: cache", report)
        live.assert_not_called()

    def test_a_filing_repeated_across_cache_days_is_only_shown_once(self):
        self._cache(
            "insider_trans", {"2026-09-05": [self.FILING], "2026-09-06": [self.FILING]}
        )
        self._live("fetch_insider_transactions_live", [])

        report = interface.get_finnhub_company_insider_transactions("NVDA", "2026-09-09", 15)

        self.assertEqual(report.count("A Director"), 1)

    def test_missing_fields_get_readable_placeholders(self):
        self._cache("insider_trans", {"2026-09-05": [{}]})
        self._live("fetch_insider_transactions_live", [])

        report = interface.get_finnhub_company_insider_transactions("NVDA", "2026-09-09", 15)

        self.assertIn("Unknown Insider", report)
        self.assertIn("Change: N/A", report)

    def test_an_empty_cache_falls_through_to_the_live_api(self):
        self._live("fetch_insider_transactions_live", [self.FILING])

        report = interface.get_finnhub_company_insider_transactions("NVDA", "2026-09-09", 15)

        self.assertIn("source: finnhub_live_api", report)
        self.assertIn("A Director", report)

    def test_offline_runs_do_not_reach_the_live_api(self):
        self._online(False)
        live = self._live("fetch_insider_transactions_live", [self.FILING])

        report = interface.get_finnhub_company_insider_transactions("NVDA", "2026-09-09", 15)

        live.assert_not_called()
        self.assertIn("No records found", report)

    def test_a_live_failure_reads_as_no_records(self):
        self._live("fetch_insider_transactions_live", RuntimeError("quota"))

        self.assertIn(
            "No records found",
            interface.get_finnhub_company_insider_transactions("NVDA", "2026-09-09", 15),
        )


class CoindeskRoutingTests(unittest.TestCase):
    def _news(self, ticker):
        captured = {}

        def util(symbol, n=5):
            captured["symbol"] = symbol
            captured["n"] = n
            return "news"

        with mock.patch.object(interface, "get_coindesk_news_util", util):
            interface.get_coindesk_news(ticker)
        return captured

    def test_a_pair_is_reduced_to_its_base_currency(self):
        self.assertEqual(self._news("BTC/USD")["symbol"], "BTC")

    def test_a_concatenated_pair_is_reduced_too(self):
        self.assertEqual(self._news("btcusd")["symbol"], "BTC")
        self.assertEqual(self._news("BTCUSDT")["symbol"], "BTC")

    def test_a_bare_symbol_passes_through(self):
        self.assertEqual(self._news("eth")["symbol"], "ETH")

    def test_the_sentence_budget_is_forwarded(self):
        with mock.patch.object(
            interface, "get_coindesk_news_util", lambda symbol, n=5: str(n)
        ):
            self.assertEqual(interface.get_coindesk_news("BTC", 3), "3")


SIMFIN_STATEMENTS = (
    (
        interface.get_simfin_balance_sheet,
        ("balance_sheet", "us-balance-{freq}.csv"),
        "balance sheet",
    ),
    (
        interface.get_simfin_cashflow,
        ("cash_flow", "us-cashflow-{freq}.csv"),
        "cash flow statement",
    ),
    (
        interface.get_simfin_income_statements,
        ("income_statements", "us-income-{freq}.csv"),
        "income statement",
    ),
)


class SimfinStatementTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)
        patcher = mock.patch.object(interface, "DATA_DIR", str(self.data_dir))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write(self, folder, filename, rows, *, freq="annual"):
        path = (
            self.data_dir
            / "fundamental_data"
            / "simfin_data_all"
            / folder
            / "companies"
            / "us"
        )
        path.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_csv(
            path / filename.format(freq=freq), sep=";", index=False
        )

    @staticmethod
    def _row(**overrides):
        row = {
            "Ticker": "NVDA",
            "SimFinId": 12345,
            "Report Date": "2025-12-31",
            "Publish Date": "2026-02-20",
            "Revenue": 100.0,
        }
        row.update(overrides)
        return row

    def test_a_missing_download_reads_as_unavailable(self):
        """The SimFin bundle is a large optional download."""
        for fetch, (_folder, _filename), label in SIMFIN_STATEMENTS:
            report = fetch("NVDA", "annual", "2026-09-09")

            self.assertIn(f"{label} for NVDA: unavailable", report)
            self.assertIn("SimFin file missing", report)

    def test_the_most_recently_published_report_is_returned(self):
        for fetch, (folder, filename), label in SIMFIN_STATEMENTS:
            self._write(
                folder,
                filename,
                [
                    self._row(**{"Publish Date": "2025-02-20", "Revenue": 50.0}),
                    self._row(**{"Publish Date": "2026-02-20", "Revenue": 100.0}),
                ],
            )

            report = fetch("NVDA", "annual", "2026-09-09")

            self.assertIn("released on 2026-02-20", report)
            self.assertIn("100.0", report)

    def test_a_report_published_after_the_run_date_is_not_used(self):
        """Reading a future filing would leak hindsight into a backtest."""
        for fetch, (folder, filename), label in SIMFIN_STATEMENTS:
            self._write(
                folder,
                filename,
                [
                    self._row(**{"Publish Date": "2026-02-20", "Revenue": 100.0}),
                    self._row(**{"Publish Date": "2026-08-20", "Revenue": 200.0}),
                ],
            )

            report = fetch("NVDA", "annual", "2026-05-01")

            self.assertIn("released on 2026-02-20", report)
            self.assertNotIn("200.0", report)

    def test_another_companys_filing_is_not_used(self):
        for fetch, (folder, filename), label in SIMFIN_STATEMENTS:
            self._write(folder, filename, [self._row(Ticker="AAPL")])

            self.assertIn(
                "no SimFin report available", fetch("NVDA", "annual", "2026-09-09")
            )

    def test_no_filing_before_the_run_date_says_so(self):
        for fetch, (folder, filename), label in SIMFIN_STATEMENTS:
            self._write(folder, filename, [self._row()])

            report = fetch("NVDA", "annual", "2020-01-01")

            self.assertIn(f"{label} for NVDA: no SimFin report available", report)

    def test_the_vendor_id_is_not_shown_to_the_model(self):
        for fetch, (folder, filename), _label in SIMFIN_STATEMENTS:
            self._write(folder, filename, [self._row()])

            self.assertNotIn("SimFinId", fetch("NVDA", "annual", "2026-09-09"))

    def test_the_frequency_selects_the_file(self):
        for fetch, (folder, filename), _label in SIMFIN_STATEMENTS:
            self._write(folder, filename, [self._row()], freq="quarterly")

            self.assertIn("released on", fetch("NVDA", "quarterly", "2026-09-09"))
            self.assertIn("unavailable", fetch("NVDA", "annual", "2026-09-09"))


if __name__ == "__main__":
    unittest.main()
