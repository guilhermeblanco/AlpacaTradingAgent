"""Tests for the screener's candidate selection.

The screener decides which symbols an unattended run will analyze and
possibly trade. Its filters are the only thing standing between a scan and
re-analyzing a position already held, re-entering one just exited, or
picking an instrument too thin to fill.
"""

from __future__ import annotations

import os
import pickle
import tempfile
import time
import unittest
from unittest import mock

import numpy as np
import pandas as pd

from tradingagents import screener


def _frame(rows=250, *, closes=None, volumes=None, opens=None, trend=0.2):
    closes = closes or [100.0 + index * trend for index in range(rows)]
    return pd.DataFrame(
        {
            "Open": opens or closes,
            "High": [value * 1.01 for value in closes],
            "Low": [value * 0.99 for value in closes],
            "Close": closes,
            "Volume": volumes or [1_000_000.0] * rows,
        },
        index=pd.date_range("2026-01-01", periods=rows, freq="D"),
    )


def _candidate(symbol, *, asset_type="stock", score=10.0, avg_volume=5_000_000.0):
    return {
        "symbol": symbol,
        "asset_type": asset_type,
        "score": score,
        "signals": {"avg_volume": avg_volume},
    }


class SignalComputationTests(unittest.TestCase):
    def test_too_little_history_yields_no_signals(self):
        self.assertIsNone(screener.compute_signals(_frame(rows=10)))

    def test_no_frame_yields_no_signals(self):
        self.assertIsNone(screener.compute_signals(None))

    def test_a_normal_frame_produces_every_signal(self):
        signals = screener.compute_signals(_frame(), symbol="NVDA")

        for key in (
            "volume_spike",
            "rsi_14",
            "prev_rsi_14",
            "sma50_reclaim",
            "sma200_reclaim",
            "bb_breakout",
            "bb_squeeze",
            "avg_volume",
            "gap_pct",
        ):
            self.assertIn(key, signals, key)

    def test_a_volume_spike_is_measured_against_the_average(self):
        volumes = [1_000_000.0] * 249 + [5_000_000.0]

        signals = screener.compute_signals(_frame(volumes=volumes))

        self.assertGreater(signals["volume_spike"], 2.0)

    def test_steady_volume_reads_as_no_spike(self):
        signals = screener.compute_signals(_frame())

        self.assertAlmostEqual(signals["volume_spike"], 1.0, places=1)

    def test_an_overnight_gap_is_measured_against_the_previous_close(self):
        closes = [100.0] * 250
        opens = [100.0] * 249 + [110.0]

        signals = screener.compute_signals(_frame(closes=closes, opens=opens))

        self.assertAlmostEqual(signals["gap_pct"], 10.0, places=1)

    def test_a_reclaim_of_the_fifty_day_average_is_detected(self):
        # Long decline, then a sharp move back above the average.
        closes = [200.0 - index * 0.5 for index in range(249)] + [400.0]

        signals = screener.compute_signals(_frame(closes=closes))

        self.assertTrue(signals["sma50_reclaim"])


class ScoringTests(unittest.TestCase):
    BASE = {
        "symbol": "NVDA",
        "price": 100.0,
        "volume_spike": 1.0,
        "rsi_14": 50.0,
        "prev_rsi_14": 50.0,
        "sma50_reclaim": False,
        "sma200_reclaim": False,
        "bb_breakout": False,
        "bb_squeeze": False,
        "avg_volume": 1_000_000.0,
        "gap_pct": 0.0,
    }

    def _score(self, **overrides):
        return screener.score_candidate({**self.BASE, **overrides})

    def test_an_unremarkable_symbol_scores_low(self):
        self.assertLess(self._score(), 2.0)

    def test_a_large_volume_spike_scores_higher_than_a_small_one(self):
        self.assertGreater(self._score(volume_spike=2.5), self._score(volume_spike=1.6))
        self.assertGreater(self._score(volume_spike=1.6), self._score(volume_spike=1.0))

    def test_an_oversold_reading_adds_to_the_score(self):
        self.assertGreater(self._score(rsi_14=25.0), self._score(rsi_14=50.0))

    def test_crossing_back_above_oversold_scores_most(self):
        """A recovery is a stronger signal than merely being oversold."""
        crossing = self._score(prev_rsi_14=30.0, rsi_14=40.0)
        still_oversold = self._score(prev_rsi_14=25.0, rsi_14=25.0)

        self.assertGreater(crossing, still_oversold)

    def test_reclaiming_the_two_hundred_day_outweighs_the_fifty_day(self):
        self.assertGreater(
            self._score(sma200_reclaim=True), self._score(sma50_reclaim=True)
        )

    def test_a_missing_rsi_does_not_break_scoring(self):
        self.assertIsInstance(self._score(rsi_14=np.nan, prev_rsi_14=np.nan), float)

    def test_signals_accumulate(self):
        combined = self._score(
            volume_spike=2.5, rsi_14=25.0, sma200_reclaim=True, bb_breakout=True
        )

        self.assertGreater(combined, self._score(volume_spike=2.5))


class FilterTests(unittest.TestCase):
    def _filter(self, candidates, **overrides):
        params = {
            "owned_symbols": set(),
            "pending_symbols": set(),
            "cooldown_map": {},
            "asset_filter": "all",
        }
        params.update(overrides)
        return screener.apply_filters(candidates, **params)

    def test_a_strong_candidate_survives(self):
        result = self._filter([_candidate("NVDA")])

        self.assertEqual([item["symbol"] for item in result], ["NVDA"])

    def test_a_symbol_already_held_is_skipped(self):
        """Re-analyzing a holding would pyramid it."""
        result = self._filter([_candidate("NVDA")], owned_symbols={"NVDA"})

        self.assertEqual(result, [])

    def test_a_symbol_already_queued_is_skipped(self):
        result = self._filter([_candidate("NVDA")], pending_symbols={"NVDA"})

        self.assertEqual(result, [])

    def test_a_symbol_inside_its_cooldown_is_skipped(self):
        """Just analyzed; re-entering immediately would churn."""
        result = self._filter(
            [_candidate("NVDA")], cooldown_map={"NVDA": time.time()}
        )

        self.assertEqual(result, [])

    def test_a_symbol_past_its_cooldown_is_allowed(self):
        long_ago = time.time() - (screener.SCREENER_COOLDOWN_HOURS + 1) * 3600

        result = self._filter([_candidate("NVDA")], cooldown_map={"NVDA": long_ago})

        self.assertEqual([item["symbol"] for item in result], ["NVDA"])

    def test_a_thin_stock_is_skipped(self):
        """Too illiquid to fill without moving the price."""
        result = self._filter([_candidate("NVDA", avg_volume=1_000.0)])

        self.assertEqual(result, [])

    def test_a_stock_with_unknown_volume_is_skipped(self):
        result = self._filter([_candidate("NVDA", avg_volume=np.nan)])

        self.assertEqual(result, [])

    def test_crypto_is_held_to_a_lower_volume_bar(self):
        result = self._filter(
            [_candidate("BTC/USD", asset_type="crypto", avg_volume=5_000.0, score=6.0)]
        )

        self.assertEqual([item["symbol"] for item in result], ["BTC/USD"])

    def test_a_weak_score_is_skipped(self):
        result = self._filter(
            [_candidate("NVDA", score=screener.SCREENER_MIN_SCORE_STOCK - 1)]
        )

        self.assertEqual(result, [])

    def test_crypto_is_held_to_a_lower_score_bar(self):
        below_stock_bar = screener.SCREENER_MIN_SCORE_CRYPTO + 0.5

        self.assertEqual(
            self._filter([_candidate("NVDA", score=below_stock_bar)]), []
        )
        self.assertTrue(
            self._filter(
                [_candidate("BTC/USD", asset_type="crypto", score=below_stock_bar)]
            )
        )

    def test_the_asset_filter_narrows_the_result(self):
        candidates = [
            _candidate("NVDA"),
            _candidate("BTC/USD", asset_type="crypto", score=6.0),
        ]

        self.assertEqual(
            [item["symbol"] for item in self._filter(candidates, asset_filter="stock")],
            ["NVDA"],
        )
        self.assertEqual(
            [item["symbol"] for item in self._filter(candidates, asset_filter="crypto")],
            ["BTC/USD"],
        )

    def test_results_are_ordered_by_score(self):
        candidates = [
            _candidate("AAPL", score=8.0),
            _candidate("NVDA", score=12.0),
            _candidate("MSFT", score=10.0),
        ]

        result = self._filter(candidates)

        self.assertEqual([item["symbol"] for item in result], ["NVDA", "MSFT", "AAPL"])

    def test_the_candidate_cap_is_honoured(self):
        candidates = [
            _candidate(f"SYM{index}", score=10.0 + index) for index in range(20)
        ]

        result = self._filter(candidates)

        self.assertEqual(len(result), screener.SCREENER_MAX_CANDIDATES)


class UniverseTests(unittest.TestCase):
    def test_the_default_universe_covers_both_asset_classes(self):
        universe = screener.load_universe()

        self.assertTrue(universe)
        self.assertIn("stock", set(universe.values()))
        self.assertIn("crypto", set(universe.values()))

    def test_a_provider_supplies_its_own_instruments(self):
        provider = mock.MagicMock()
        provider.search_instruments.return_value = []

        universe = screener.load_universe(instrument_provider=provider)

        self.assertTrue(universe)

    def test_a_failing_provider_falls_back_to_the_defaults(self):
        provider = mock.MagicMock()
        provider.search_instruments.side_effect = RuntimeError("no credentials")

        universe = screener.load_universe(instrument_provider=provider)

        self.assertTrue(universe)


class ScanStatusTests(unittest.TestCase):
    def test_the_status_is_readable_before_any_scan(self):
        """Empty until the first scan populates it."""
        status = screener.get_scan_status()

        self.assertIsInstance(status, dict)


if __name__ == "__main__":
    unittest.main()


class Instrument:
    def __init__(self, symbol, asset_type="stock", tradable=True):
        self.symbol = symbol
        self.asset_type = asset_type
        self.tradable = tradable


class Provider:
    def __init__(self, instruments=(), error=None):
        self.instruments = list(instruments)
        self.error = error
        self.calls = []

    def search_instruments(self, query, limit=None):
        self.calls.append((query, limit))
        if self.error:
            raise self.error
        return self.instruments


class UniverseTests(unittest.TestCase):
    """The universe is what the broker says it can trade, widened so the
    curated defaults are never silently dropped."""

    def test_the_brokers_tradeable_instruments_become_the_universe(self):
        universe = screener.load_universe(
            Provider([Instrument("SOMETHING"), Instrument("BTC/USD", "crypto")])
        )

        self.assertEqual(universe["SOMETHING"], "stock")
        self.assertEqual(universe["BTC/USD"], "crypto")

    def test_untradeable_instruments_are_excluded(self):
        universe = screener.load_universe(
            Provider([Instrument("HALTED", tradable=False), Instrument("AAPL")])
        )

        self.assertNotIn("HALTED", universe)

    def test_the_curated_defaults_are_always_present(self):
        universe = screener.load_universe(Provider([Instrument("SOMETHING")]))

        self.assertIn("AAPL", universe)
        self.assertIn("BTC/USD", universe)

    def test_default_crypto_is_normalized_to_slash_form(self):
        """The broker and the analysts both spell pairs BASE/USD."""
        universe = screener.load_universe(Provider([Instrument("SOMETHING")]))

        self.assertNotIn("BTC-USD", universe)
        self.assertEqual(universe["BTC/USD"], "crypto")

    def test_the_brokers_own_spelling_of_a_default_is_not_duplicated(self):
        universe = screener.load_universe(
            Provider([Instrument("BTC/USD", "crypto")])
        )

        self.assertEqual(
            [k for k in universe if k.startswith("BTC")], ["BTC/USD"]
        )

    def test_an_unreachable_broker_falls_back_to_the_defaults(self):
        universe = screener.load_universe(Provider(error=RuntimeError("no broker")))

        self.assertIn("AAPL", universe)
        self.assertIn("BTC/USD", universe)

    def test_an_empty_instrument_list_falls_back_to_the_defaults(self):
        universe = screener.load_universe(Provider([]))

        self.assertIn("AAPL", universe)

    def test_the_universe_can_be_capped(self):
        with mock.patch.object(screener, "SCREENER_MAX_UNIVERSE", 5):
            universe = screener.load_universe(
                Provider([Instrument(f"T{i}") for i in range(50)])
            )

        self.assertEqual(len(universe), 5)

    def test_the_whole_market_is_returned_when_no_cap_is_set(self):
        with mock.patch.object(screener, "SCREENER_MAX_UNIVERSE", 0):
            universe = screener.load_universe(
                Provider([Instrument(f"T{i}") for i in range(50)])
            )

        self.assertGreater(len(universe), 50)


class BatchDownloadTests(unittest.TestCase):
    def _download(self, frame, **kwargs):
        captured = {}

        def download(tickers, **options):
            captured["tickers"] = tickers
            captured.update(options)
            if isinstance(frame, Exception):
                raise frame
            return frame

        with mock.patch.object(screener.yf, "download", download):
            result = screener._download_batch(**kwargs)
        return result, captured

    def test_nothing_requested_downloads_nothing(self):
        with mock.patch.object(screener.yf, "download") as download:
            self.assertEqual(screener._download_batch([], "250d", "1d"), {})

        download.assert_not_called()

    def test_a_pair_is_requested_in_yahoo_spelling_and_returned_in_ours(self):
        frame = _frame(30)

        result, captured = self._download(
            frame, tickers=["BTC/USD"], period="250d", interval="1d"
        )

        self.assertEqual(captured["tickers"], ["BTC-USD"])
        self.assertEqual(list(result), ["BTC/USD"])

    def test_an_empty_single_ticker_frame_yields_nothing(self):
        result, _captured = self._download(
            pd.DataFrame(), tickers=["AAPL"], period="250d", interval="1d"
        )

        self.assertEqual(result, {})

    def test_a_multi_ticker_frame_is_split_per_symbol(self):
        frame = pd.concat(
            {"AAPL": _frame(30), "MSFT": _frame(30)}, axis=1
        )

        result, _captured = self._download(
            frame, tickers=["AAPL", "MSFT"], period="250d", interval="1d"
        )

        self.assertEqual(sorted(result), ["AAPL", "MSFT"])

    def test_a_symbol_missing_from_the_response_is_skipped(self):
        frame = pd.concat({"AAPL": _frame(30)}, axis=1)

        result, _captured = self._download(
            frame, tickers=["AAPL", "MSFT"], period="250d", interval="1d"
        )

        self.assertEqual(list(result), ["AAPL"])

    def test_a_failed_download_yields_nothing_rather_than_raising(self):
        result, _captured = self._download(
            RuntimeError("yahoo down"), tickers=["AAPL"], period="250d", interval="1d"
        )

        self.assertEqual(result, {})


class BulkFetchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        cache_file = os.path.join(self._tmp.name, "ohlcv_cache.pkl")
        for name, value in (
            ("_CACHE_DIR", self._tmp.name),
            ("_OHLCV_CACHE_FILE", cache_file),
        ):
            patcher = mock.patch.object(screener, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.cache_file = cache_file
        sleep = mock.patch("time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def _seed_cache(self, data):
        with open(self.cache_file, "wb") as fh:
            pickle.dump(data, fh)

    def _fetch(self, tickers, batches):
        seen = []

        def download_batch(batch, period, interval):
            seen.append((tuple(batch), period))
            return batches.get(period, {})

        with mock.patch.object(screener, "_download_batch", download_batch):
            return screener.fetch_bulk_ohlcv(tickers), seen

    def test_an_uncached_ticker_is_downloaded_in_full(self):
        result, seen = self._fetch(["AAPL"], {"250d": {"AAPL": _frame(120)}})

        self.assertEqual(seen, [(("AAPL",), "250d")])
        self.assertEqual(list(result), ["AAPL"])

    def test_a_well_stocked_cache_only_asks_for_the_recent_days(self):
        self._seed_cache({"AAPL": _frame(150)})

        _result, seen = self._fetch(["AAPL"], {"5d": {"AAPL": _frame(5)}})

        self.assertEqual(seen, [(("AAPL",), "5d")])

    def test_a_thin_cache_entry_is_refetched_in_full(self):
        """Below 100 rows there is not enough history for an SMA200 read."""
        self._seed_cache({"AAPL": _frame(20)})

        _result, seen = self._fetch(["AAPL"], {"250d": {"AAPL": _frame(120)}})

        self.assertEqual(seen[0][1], "250d")

    def test_an_incremental_update_is_merged_onto_the_cached_history(self):
        cached = _frame(150)
        self._seed_cache({"AAPL": cached})
        update = _frame(5)
        update.index = pd.date_range(
            cached.index[-1] + pd.Timedelta(days=1), periods=5, freq="D"
        )

        result, _seen = self._fetch(["AAPL"], {"5d": {"AAPL": update}})

        self.assertGreater(len(result["AAPL"]), len(update))
        self.assertEqual(result["AAPL"].index[-1], update.index[-1])

    def test_overlapping_rows_keep_the_fresher_copy(self):
        cached = _frame(150)
        self._seed_cache({"AAPL": cached})
        update = cached.tail(5).copy()
        update["Close"] = 999.0

        result, _seen = self._fetch(["AAPL"], {"5d": {"AAPL": update}})

        self.assertEqual(result["AAPL"]["Close"].iloc[-1], 999.0)
        self.assertFalse(result["AAPL"].index.duplicated().any())

    def test_the_history_is_capped_at_two_hundred_rows(self):
        result, _seen = self._fetch(["AAPL"], {"250d": {"AAPL": _frame(400)}})

        self.assertEqual(len(result["AAPL"]), 200)

    def test_a_ticker_nothing_could_be_fetched_for_is_omitted(self):
        result, _seen = self._fetch(["AAPL"], {"250d": {}})

        self.assertEqual(result, {})

    def test_the_result_is_written_back_to_the_cache(self):
        self._fetch(["AAPL"], {"250d": {"AAPL": _frame(120)}})

        with open(self.cache_file, "rb") as fh:
            self.assertIn("AAPL", pickle.load(fh))

    def test_an_unreadable_cache_is_treated_as_empty(self):
        with open(self.cache_file, "wb") as fh:
            fh.write(b"not a pickle")

        result, seen = self._fetch(["AAPL"], {"250d": {"AAPL": _frame(120)}})

        self.assertEqual(seen[0][1], "250d")
        self.assertEqual(list(result), ["AAPL"])

    def test_downloads_are_batched(self):
        tickers = [f"T{i}" for i in range(120)]

        _result, seen = self._fetch(tickers, {"250d": {}})

        self.assertEqual([len(batch) for batch, _period in seen], [50, 50, 20])


class ScanTests(unittest.TestCase):
    def _scan(self, universe, ohlcv, **kwargs):
        with mock.patch.object(screener, "load_universe", lambda: dict(universe)):
            with mock.patch.object(
                screener, "fetch_bulk_ohlcv", lambda tickers: {
                    t: ohlcv[t] for t in tickers if t in ohlcv
                }
            ):
                return screener.run_scan(**kwargs)

    def test_a_scoring_candidate_comes_back_with_its_signals(self):
        result = self._scan({"AAPL": "stock"}, {"AAPL": _frame(120)})

        self.assertEqual(result["tickers_scanned"], 1)
        self.assertEqual(result["asset_filter"], "all")
        self.assertIn("scan_time", result)
        self.assertEqual(result["all_scored"][0]["symbol"], "AAPL")
        self.assertIn("rsi_14", result["all_scored"][0])

    def test_the_asset_filter_narrows_the_universe(self):
        result = self._scan(
            {"AAPL": "stock", "BTC/USD": "crypto"},
            {"AAPL": _frame(120), "BTC/USD": _frame(120)},
            asset_filter="crypto",
        )

        self.assertEqual(
            [row["symbol"] for row in result["all_scored"]], ["BTC/USD"]
        )

    def test_a_frame_too_short_to_score_is_skipped(self):
        result = self._scan({"AAPL": "stock"}, {"AAPL": _frame(5)})

        self.assertEqual(result["all_scored"], [])

    def test_an_empty_frame_is_skipped(self):
        result = self._scan({"AAPL": "stock"}, {"AAPL": pd.DataFrame()})

        self.assertEqual(result["all_scored"], [])

    def test_results_are_ranked_by_score(self):
        result = self._scan(
            {"A": "stock", "B": "stock"},
            {"A": _frame(120), "B": _frame(120, trend=2.0)},
        )

        scores = [row["score"] for row in result["all_scored"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_only_the_top_twenty_are_reported_in_full(self):
        symbols = {f"T{i}": "stock" for i in range(25)}
        frames = {sym: _frame(120) for sym in symbols}

        result = self._scan(symbols, frames)

        self.assertEqual(len(result["all_scored"]), 20)
        self.assertEqual(result["tickers_scanned"], 25)

    def test_an_owned_symbol_is_not_offered_as_a_candidate(self):
        result = self._scan(
            {"AAPL": "stock"}, {"AAPL": _frame(120, trend=2.0)}, owned_symbols={"AAPL"}
        )

        self.assertEqual(
            [row["symbol"] for row in result["candidates"]], []
        )

    def test_the_scan_result_is_readable_afterwards(self):
        result = self._scan({"AAPL": "stock"}, {"AAPL": _frame(120)})

        self.assertEqual(screener.get_scan_status(), result)
