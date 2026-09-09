"""Tests for the stockstats indicator bridge.

`get_stock_stats` is what the market analyst reaches for when a technical
brief is unavailable. It caches to disk, normalizes column casing for
stockstats, and must return a readable "N/A: ..." string rather than raise
whenever the data behind an indicator is missing or unusable.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from tradingagents.dataflows.stockstats_utils import StockstatsUtils


def _bars(rows=250, *, lowercase=True):
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range("2025-01-01", periods=rows, freq="D"),
            "open": [100.0 + index * 0.1 for index in range(rows)],
            "high": [101.0 + index * 0.1 for index in range(rows)],
            "low": [99.0 + index * 0.1 for index in range(rows)],
            "close": [100.5 + index * 0.1 for index in range(rows)],
            "volume": [1_000_000 + index for index in range(rows)],
        }
    )
    if not lowercase:
        frame = frame.rename(
            columns={
                "timestamp": "Date",
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "volume": "Volume",
            }
        )
    return frame


class OnlineIndicatorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = self._tmp.name

    def _run(self, frame, *, indicator="rsi_14", symbol="NVDA"):
        provider = mock.MagicMock()
        provider.name = "alpaca"
        provider.get_bars.return_value = frame
        with mock.patch(
            "tradingagents.dataflows.stockstats_utils.get_config",
            lambda: {"data_cache_dir": self.cache},
        ), mock.patch(
            "tradingagents.marketdata.get_research_market_data_provider",
            lambda _config: provider,
        ):
            return StockstatsUtils.get_stock_stats(
                symbol, indicator, "2025-09-01", self.cache, online=True
            )

    def test_an_indicator_is_computed_from_provider_bars(self):
        result = self._run(_bars())

        self.assertNotIsInstance(result, str)

    def test_every_supported_indicator_computes(self):
        """wrap() lowercases columns, so a frame carrying both Close and
        close made every one of these fail with "Expected a single column"."""
        for indicator in ("rsi_14", "close_50_sma", "macd", "obv", "atr_14"):
            result = self._run(_bars(), indicator=indicator)

            self.assertNotIsInstance(result, str, f"{indicator}: {result}")
            self.assertIsInstance(float(result), float)

    def test_no_data_is_reported_rather_than_raised(self):
        result = self._run(pd.DataFrame())

        self.assertIsInstance(result, str)
        self.assertIn("No data available", result)

    def test_too_little_history_is_reported(self):
        """A 50-period average over 30 bars would be noise."""
        result = self._run(_bars(rows=30))

        self.assertIsInstance(result, str)
        self.assertIn("Insufficient data", result)

    def test_the_fetch_is_written_to_the_cache(self):
        self._run(_bars())

        cached = list(Path(self.cache).glob("*.csv"))

        self.assertTrue(cached)
        self.assertIn("NVDA", cached[0].name)

    def test_a_second_call_reads_the_cache_instead_of_the_provider(self):
        provider = mock.MagicMock()
        provider.name = "alpaca"
        provider.get_bars.return_value = _bars()

        with mock.patch(
            "tradingagents.dataflows.stockstats_utils.get_config",
            lambda: {"data_cache_dir": self.cache},
        ), mock.patch(
            "tradingagents.marketdata.get_research_market_data_provider",
            lambda _config: provider,
        ):
            for _ in range(2):
                StockstatsUtils.get_stock_stats(
                    "NVDA", "rsi_14", "2025-09-01", self.cache, online=True
                )

        self.assertEqual(provider.get_bars.call_count, 1)

    def test_a_crypto_symbol_gets_a_filesystem_safe_cache_name(self):
        """BTC/USD would otherwise be read as a directory separator."""
        self._run(_bars(), symbol="BTC/USD")

        cached = list(Path(self.cache).glob("*.csv"))

        self.assertTrue(cached)
        self.assertNotIn("/", cached[0].name)

    def test_a_malformed_cache_file_is_reported(self):
        cache_file = next(
            iter(
                [
                    Path(self.cache) / name
                    for name in ["seed.csv"]
                ]
            )
        )
        self._run(_bars())
        existing = next(Path(self.cache).glob("*.csv"))
        existing.write_text("not,a,valid\nohlcv,file,here\n", encoding="utf-8")

        result = self._run(_bars())

        self.assertIsInstance(result, str)
        self.assertIn("Malformed cached", result)
        self.assertFalse(cache_file.exists())

    def test_capitalized_provider_columns_are_accepted(self):
        result = self._run(_bars(lowercase=False))

        self.assertNotIsInstance(result, str)

    def test_an_unknown_indicator_is_reported_rather_than_raised(self):
        result = self._run(_bars(), indicator="not_an_indicator")

        self.assertIsInstance(result, str)


class OfflineIndicatorTests(unittest.TestCase):
    def test_missing_offline_data_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Exception) as raised:
                StockstatsUtils.get_stock_stats(
                    "NVDA", "rsi_14", "2025-09-01", tmp, online=False
                )

        self.assertIn("not fetched yet", str(raised.exception))

    def test_offline_data_is_read_from_the_expected_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "NVDA-Alpaca-data-2015-01-01-2025-03-25.csv"
            _bars(lowercase=False).to_csv(path, index=False, encoding="utf-8")

            result = StockstatsUtils.get_stock_stats(
                "NVDA", "rsi_14", "2025-09-01", tmp, online=False
            )

        self.assertIsNotNone(result)

    def test_offline_indicators_return_values_not_none(self):
        """The calculation used to sit inside the online branch, so an
        offline call fell off the end and returned None for every date."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "NVDA-Alpaca-data-2015-01-01-2025-03-25.csv"
            _bars(lowercase=False).to_csv(path, index=False, encoding="utf-8")

            for indicator in ("rsi_14", "close_50_sma", "macd", "obv", "atr_14"):
                result = StockstatsUtils.get_stock_stats(
                    "NVDA", indicator, "2025-09-01", tmp, online=False
                )

                self.assertIsNotNone(result, indicator)
                self.assertNotIsInstance(result, str, f"{indicator}: {result}")

    def test_offline_too_little_history_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "NVDA-Alpaca-data-2015-01-01-2025-03-25.csv"
            _bars(rows=20, lowercase=False).to_csv(path, index=False, encoding="utf-8")

            result = StockstatsUtils.get_stock_stats(
                "NVDA", "rsi_14", "2025-09-01", tmp, online=False
            )

        self.assertIn("Insufficient data", result)


if __name__ == "__main__":
    unittest.main()


class ManualIndicatorTests(unittest.TestCase):
    """OBV, ATR, and the moving averages are computed here rather than by
    stockstats, because stockstats' own versions choke on the frames the
    providers return."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = self._tmp.name

    def _run(self, indicator, *, frame=None, curr_date="2025-09-01"):
        provider = mock.MagicMock()
        provider.name = "alpaca"
        provider.get_bars.return_value = frame if frame is not None else _bars()
        with mock.patch(
            "tradingagents.dataflows.stockstats_utils.get_config",
            lambda: {"data_cache_dir": self.cache},
        ), mock.patch(
            "tradingagents.marketdata.get_research_market_data_provider",
            lambda _config: provider,
        ):
            return StockstatsUtils.get_stock_stats(
                "NVDA", indicator, curr_date, self.cache, online=True
            )

    def test_an_exponential_average_is_computed(self):
        value = self._run("close_8_ema")

        self.assertIsInstance(value, float)

    def test_a_simple_average_is_computed(self):
        value = self._run("close_50_sma")

        self.assertIsInstance(value, float)

    def test_the_true_range_average_is_computed(self):
        value = self._run("atr_14")

        self.assertIsInstance(value, float)
        self.assertGreater(value, 0)

    def test_on_balance_volume_is_computed(self):
        value = self._run("obv")

        self.assertIsInstance(value, float)

    def test_a_malformed_average_name_is_reported_rather_than_raised(self):
        for indicator in ("close_x_ema", "close_y_sma"):
            answer = self._run(indicator)

            self.assertIsInstance(answer, str)
            self.assertTrue(answer.startswith("N/A:"), answer)

    def test_a_date_before_any_data_is_reported(self):
        answer = self._run("rsi_14", curr_date="2020-01-01")

        self.assertIsInstance(answer, str)
        self.assertIn("No trading data available", answer)

    def test_a_non_trading_day_falls_back_to_the_prior_session(self):
        """The analyst asks about today; the market may have been shut."""
        frame = _bars(rows=200)
        frame = frame[frame["timestamp"] != pd.Timestamp("2025-06-01")]

        answer = self._run("rsi_14", frame=frame, curr_date="2025-06-01")

        self.assertIsInstance(answer, str)
        self.assertIn("as of", answer)

    def test_an_average_longer_than_the_history_reports_not_calculable(self):
        """A 200-day average over 120 sessions is NaN, not zero."""
        answer = self._run("close_200_sma", frame=_bars(rows=120), curr_date="2025-02-01")

        self.assertIsInstance(answer, str)
        self.assertIn("not calculable", answer)
