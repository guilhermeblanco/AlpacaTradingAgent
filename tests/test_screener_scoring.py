"""Tests for the screener's candidate selection.

The screener decides which symbols an unattended run will analyze and
possibly trade. Its filters are the only thing standing between a scan and
re-analyzing a position already held, re-entering one just exited, or
picking an instrument too thin to fill.
"""

from __future__ import annotations

import time
import unittest
from unittest import mock

import numpy as np
import pandas as pd

from tradingagents import screener


def _frame(rows=250, *, closes=None, volumes=None, opens=None):
    closes = closes or [100.0 + index * 0.2 for index in range(rows)]
    return pd.DataFrame(
        {
            "Open": opens or closes,
            "High": [value * 1.01 for value in closes],
            "Low": [value * 0.99 for value in closes],
            "Close": closes,
            "Volume": volumes or [1_000_000.0] * rows,
        }
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
