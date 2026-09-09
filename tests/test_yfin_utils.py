"""Tests for the yfinance wrapper.

Every method here is reached through `init_ticker`, which swaps the symbol
string for a `yf.Ticker` before the body runs — so the bodies read a ticker
object even though callers pass a string. The retry wrapper in front of
price history is the only real logic, and it exists because yfinance
answers with an empty frame rather than an error when it is rate limited.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

import pandas as pd

from tradingagents.dataflows import yfin_utils
from tradingagents.dataflows.yfin_utils import YFinanceUtils, _history_with_retry


def _bars(rows=3):
    return pd.DataFrame(
        {
            "Open": [100.0] * rows,
            "High": [101.0] * rows,
            "Low": [99.0] * rows,
            "Close": [100.5] * rows,
            "Volume": [1_000] * rows,
        },
        index=pd.date_range("2026-01-02", periods=rows, freq="D"),
    )


class FakeTicker:
    def __init__(self, **attrs):
        self.ticker = attrs.pop("ticker", "NVDA")
        self.history_calls = []
        self._history = attrs.pop("history", _bars())
        self._history_error = attrs.pop("history_error", None)
        for name, value in attrs.items():
            setattr(self, name, value)

    def history(self, **kwargs):
        self.history_calls.append(kwargs)
        if self._history_error:
            raise self._history_error
        if callable(self._history):
            return self._history(len(self.history_calls))
        return self._history


class HistoryRetryTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(yfin_utils.time, "sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_good_answer_is_returned_without_retrying(self):
        ticker = FakeTicker()

        result = _history_with_retry(ticker, start="2026-01-01", end="2026-01-05")

        self.assertEqual(len(ticker.history_calls), 1)
        self.assertFalse(result.empty)

    def test_prices_are_requested_unadjusted(self):
        """Indicators are computed on raw OHLCV, not split-adjusted series."""
        ticker = FakeTicker()

        _history_with_retry(ticker, start="2026-01-01", end="2026-01-05")

        self.assertFalse(ticker.history_calls[0]["auto_adjust"])

    def test_an_empty_frame_is_retried(self):
        """Rate limiting shows up as an empty frame, not an exception."""
        ticker = FakeTicker(
            history=lambda attempt: pd.DataFrame() if attempt == 1 else _bars()
        )

        result = _history_with_retry(ticker, start="2026-01-01", end="2026-01-05")

        self.assertEqual(len(ticker.history_calls), 2)
        self.assertFalse(result.empty)

    def test_a_frame_without_closes_is_retried(self):
        ticker = FakeTicker(
            history=lambda attempt: (
                pd.DataFrame({"Open": [1.0]}) if attempt == 1 else _bars()
            )
        )

        result = _history_with_retry(ticker, start="2026-01-01", end="2026-01-05")

        self.assertIn("Close", result.columns)

    def test_a_raising_call_is_retried(self):
        calls = []

        def history(attempt):
            calls.append(attempt)
            if attempt == 1:
                raise ConnectionError("connection reset")
            return _bars()

        ticker = FakeTicker(history=history)

        self.assertFalse(
            _history_with_retry(ticker, start="2026-01-01", end="2026-01-05").empty
        )

    def test_exhausting_the_attempts_reports_the_symbol_and_the_cause(self):
        ticker = FakeTicker(history_error=ConnectionError("connection reset"))

        with self.assertRaises(RuntimeError) as raised:
            _history_with_retry(ticker, start="2026-01-01", end="2026-01-05")

        self.assertIn("NVDA", str(raised.exception))
        self.assertIn("connection reset", str(raised.exception))
        self.assertEqual(len(ticker.history_calls), 3)

    def test_the_backoff_grows_between_attempts(self):
        ticker = FakeTicker(history_error=ConnectionError("boom"))

        with self.assertRaises(RuntimeError):
            _history_with_retry(ticker, start="2026-01-01", end="2026-01-05")

        self.assertEqual(
            [call.args[0] for call in self.sleep.call_args_list], [0.5, 1.0]
        )

    def test_the_last_attempt_does_not_sleep_for_nothing(self):
        ticker = FakeTicker(history_error=ConnectionError("boom"))

        with self.assertRaises(RuntimeError):
            _history_with_retry(
                ticker, start="2026-01-01", end="2026-01-05", attempts=1
            )

        self.sleep.assert_not_called()


class TickerInjectionTests(unittest.TestCase):
    """`init_ticker` is what lets callers pass a symbol string."""

    def _patched(self, ticker):
        return mock.patch.object(yfin_utils.yf, "Ticker", return_value=ticker)

    def test_the_symbol_becomes_a_ticker_object(self):
        made = []

        def factory(symbol):
            made.append(symbol)
            return FakeTicker()

        with mock.patch.object(yfin_utils.yf, "Ticker", factory):
            YFinanceUtils.get_stock_data("NVDA", "2026-01-01", "2026-01-05")

        self.assertEqual(made, ["NVDA"])

    def test_the_end_date_is_made_inclusive(self):
        """yfinance treats `end` as exclusive; callers mean the day itself."""
        ticker = FakeTicker()

        with self._patched(ticker):
            YFinanceUtils.get_stock_data("NVDA", "2026-01-01", "2026-01-05")

        self.assertEqual(ticker.history_calls[0]["end"], "2026-01-06")
        self.assertEqual(ticker.history_calls[0]["start"], "2026-01-01")

    def test_stock_info_is_passed_through(self):
        ticker = FakeTicker(info={"shortName": "NVIDIA"})

        with self._patched(ticker):
            self.assertEqual(YFinanceUtils.get_stock_info("NVDA")["shortName"], "NVIDIA")


class CompanyInfoTests(unittest.TestCase):
    INFO = {
        "shortName": "NVIDIA",
        "industry": "Semiconductors",
        "sector": "Technology",
        "country": "United States",
        "website": "https://nvidia.com",
    }

    def test_the_selected_fields_become_a_one_row_frame(self):
        with mock.patch.object(
            yfin_utils.yf, "Ticker", return_value=FakeTicker(info=self.INFO)
        ):
            frame = YFinanceUtils.get_company_info("NVDA")

        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.iloc[0]["Company Name"], "NVIDIA")
        self.assertEqual(frame.iloc[0]["Sector"], "Technology")

    def test_missing_fields_read_as_not_available(self):
        with mock.patch.object(
            yfin_utils.yf, "Ticker", return_value=FakeTicker(info={})
        ):
            frame = YFinanceUtils.get_company_info("NVDA")

        self.assertEqual(frame.iloc[0]["Industry"], "N/A")

    def test_a_save_path_writes_a_csv(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "info.csv"
            with mock.patch.object(
                yfin_utils.yf, "Ticker", return_value=FakeTicker(info=self.INFO)
            ):
                YFinanceUtils.get_company_info("NVDA", save_path=str(path))

            self.assertIn("NVIDIA", path.read_text(encoding="utf-8"))


class StatementTests(unittest.TestCase):
    CASES = (
        ("get_income_stmt", "financials"),
        ("get_balance_sheet", "balance_sheet"),
        ("get_cash_flow", "cashflow"),
    )

    def test_each_statement_comes_from_its_yfinance_attribute(self):
        for method, attribute in self.CASES:
            frame = pd.DataFrame({attribute: [1.0]})
            ticker = FakeTicker(**{attribute: frame})

            with mock.patch.object(yfin_utils.yf, "Ticker", return_value=ticker):
                self.assertIs(getattr(YFinanceUtils, method)("NVDA"), frame)


class DividendTests(unittest.TestCase):
    def test_dividends_are_passed_through(self):
        series = pd.Series([0.01, 0.02])

        with mock.patch.object(
            yfin_utils.yf, "Ticker", return_value=FakeTicker(dividends=series)
        ):
            self.assertIs(YFinanceUtils.get_stock_dividends("NVDA"), series)

    def test_a_save_path_writes_a_csv(self):
        import tempfile
        from pathlib import Path

        series = pd.Series([0.01], name="Dividends")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "div.csv"
            with mock.patch.object(
                yfin_utils.yf, "Ticker", return_value=FakeTicker(dividends=series)
            ):
                YFinanceUtils.get_stock_dividends("NVDA", save_path=str(path))

            self.assertTrue(path.exists())


class AnalystRecommendationTests(unittest.TestCase):
    def _recommendations(self, **counts):
        return pd.DataFrame([{"period": "0m", **counts}])

    def test_the_most_voted_rating_wins(self):
        frame = self._recommendations(strongBuy=12, buy=5, hold=2, sell=0)

        with mock.patch.object(
            yfin_utils.yf, "Ticker", return_value=FakeTicker(recommendations=frame)
        ):
            rating, votes = YFinanceUtils.get_analyst_recommendations("NVDA")

        self.assertEqual(rating, "strongBuy")
        self.assertEqual(votes, 12)

    def test_the_period_column_is_not_counted_as_a_rating(self):
        frame = self._recommendations(buy=1)

        with mock.patch.object(
            yfin_utils.yf, "Ticker", return_value=FakeTicker(recommendations=frame)
        ):
            rating, _votes = YFinanceUtils.get_analyst_recommendations("NVDA")

        self.assertEqual(rating, "buy")

    def test_a_tie_resolves_to_the_first_rating(self):
        frame = self._recommendations(strongBuy=3, buy=3)

        with mock.patch.object(
            yfin_utils.yf, "Ticker", return_value=FakeTicker(recommendations=frame)
        ):
            rating, votes = YFinanceUtils.get_analyst_recommendations("NVDA")

        self.assertEqual((rating, votes), ("strongBuy", 3))

    def test_no_coverage_reads_as_no_rating(self):
        with mock.patch.object(
            yfin_utils.yf,
            "Ticker",
            return_value=FakeTicker(recommendations=pd.DataFrame()),
        ):
            self.assertEqual(
                YFinanceUtils.get_analyst_recommendations("NVDA"), (None, 0)
            )


if __name__ == "__main__":
    unittest.main()
