"""Tests for the deterministic technical analysis engine.

`technical_brief.py` is Tier 1: it computes indicators, classifies regime,
extracts levels, and emits the fixed JSON contract the market analyst reads.
No LLM is involved, so every output here is exactly reproducible — and a
silent change in it moves what the analyst is told about the market.
"""

from __future__ import annotations

import unittest
from unittest import mock

import numpy as np
import pandas as pd

from tradingagents.dataflows import technical_brief as tb
from tradingagents.dataflows.ta_schema import (
    Direction,
    KeyLevel,
    MarketStructure,
    MomentumState,
    SignalSummary,
    Strength,
    TimeframeBrief,
    TrendState,
    VolatilityState,
    VolumeState,
    VWAPState,
)


def _frame(closes, *, highs=None, lows=None, volumes=None, opens=None):
    closes = list(closes)
    count = len(closes)
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=count, freq="D"),
            "open": opens if opens is not None else closes,
            "high": highs if highs is not None else [value * 1.01 for value in closes],
            "low": lows if lows is not None else [value * 0.99 for value in closes],
            "close": closes,
            "volume": volumes if volumes is not None else [1_000.0] * count,
        }
    )


def _indicators(df):
    """Run the indicator pass without going near a market data provider."""
    provider = mock.MagicMock()
    provider.get_bars.return_value = df
    with mock.patch(
        "tradingagents.marketdata.get_research_market_data_provider",
        lambda *a, **k: provider,
    ):
        return tb.compute_indicators("NVDA", "2026-06-01", "1d")


def _rising(count=120, start=100.0, step=1.0):
    return [start + index * step for index in range(count)]


def _falling(count=120, start=220.0, step=1.0):
    return [start - index * step for index in range(count)]


def _flat(count=120, value=100.0):
    # A dead-flat series makes several indicators degenerate; a tiny
    # oscillation keeps it realistic while staying directionless.
    return [value + (1 if index % 2 else -1) * 0.05 for index in range(count)]


class IndicatorMathTests(unittest.TestCase):
    def test_sma_averages_the_window(self):
        series = pd.Series([1.0, 2.0, 3.0, 4.0])

        result = tb._sma(series, 2)

        self.assertTrue(pd.isna(result.iloc[0]))
        self.assertAlmostEqual(result.iloc[1], 1.5)
        self.assertAlmostEqual(result.iloc[3], 3.5)

    def test_ema_seeds_on_the_first_value(self):
        series = pd.Series([10.0, 20.0])

        result = tb._ema(series, 2)

        self.assertAlmostEqual(result.iloc[0], 10.0)
        self.assertGreater(result.iloc[1], 10.0)
        self.assertLess(result.iloc[1], 20.0)

    def test_rsi_saturates_high_on_an_unbroken_advance(self):
        result = tb._rsi(pd.Series(_rising(60)), 14)

        self.assertGreater(result.iloc[-1], 95)

    def test_rsi_saturates_low_on_an_unbroken_decline(self):
        result = tb._rsi(pd.Series(_falling(60)), 14)

        self.assertLess(result.iloc[-1], 5)

    def test_rsi_stays_within_bounds(self):
        result = tb._rsi(pd.Series(_flat(80)), 14).dropna()

        self.assertTrue(((result >= 0) & (result <= 100)).all())

    def test_macd_histogram_is_the_line_minus_its_signal(self):
        close = pd.Series(_rising(80))

        line, signal, histogram = tb._macd(close)

        self.assertTrue(np.allclose(histogram.values, (line - signal).values))

    def test_macd_line_is_positive_while_price_rises(self):
        line, _signal, _hist = tb._macd(pd.Series(_rising(80)))

        self.assertGreater(line.iloc[-1], 0)

    def test_atr_is_never_negative(self):
        df = _frame(_rising(60))

        result = tb._atr(df["high"], df["low"], df["close"], 14).dropna()

        self.assertTrue((result >= 0).all())

    def test_bollinger_bands_straddle_the_mean(self):
        close = pd.Series(_flat(60))

        upper, lower, bandwidth = tb._bollinger(close, 20)

        self.assertGreater(upper.iloc[-1], lower.iloc[-1])
        self.assertGreater(bandwidth.iloc[-1], 0)

    def test_obv_accumulates_volume_in_the_direction_of_change(self):
        close = pd.Series([10.0, 11.0, 12.0, 11.0])
        volume = pd.Series([100.0, 100.0, 100.0, 100.0])

        result = tb._obv(close, volume)

        self.assertAlmostEqual(result.iloc[0], 0.0)
        self.assertAlmostEqual(result.iloc[2], 200.0)
        self.assertAlmostEqual(result.iloc[3], 100.0)

    def test_stoch_rsi_stays_within_bounds(self):
        k_line, d_line = tb._stoch_rsi(pd.Series(_flat(90)))

        for series in (k_line.dropna(), d_line.dropna()):
            self.assertTrue(((series >= 0) & (series <= 100)).all())

    def test_adx_is_bounded(self):
        df = _frame(_rising(90))

        result = tb._adx(df["high"], df["low"], df["close"], 14).dropna()

        self.assertTrue(((result >= 0) & (result <= 100)).all())


class ComputeIndicatorsTests(unittest.TestCase):
    def test_every_documented_column_is_produced(self):
        result = _indicators(_frame(_rising(150)))

        for column in (
            "ema_8",
            "ema_21",
            "sma_50",
            "sma_200",
            "adx_14",
            "rsi_14",
            "stoch_k",
            "stoch_d",
            "macd",
            "macds",
            "macdh",
            "atr_14",
            "boll_ub",
            "boll_lb",
            "boll_bw",
            "obv",
            "vol_sma_20",
            "vwap",
        ):
            self.assertIn(column, result.columns, column)

    def test_too_few_bars_yields_nothing(self):
        """Indicators over a handful of bars would be noise."""
        self.assertIsNone(_indicators(_frame(_rising(10))))

    def test_no_data_yields_nothing(self):
        self.assertIsNone(_indicators(pd.DataFrame()))

    def test_a_missing_price_column_yields_nothing(self):
        df = _frame(_rising(60)).drop(columns=["high"])

        self.assertIsNone(_indicators(df))

    def test_column_names_are_normalized_to_lower_case(self):
        df = _frame(_rising(60))
        df.columns = [name.upper() for name in df.columns]

        result = _indicators(df)

        self.assertIn("close", result.columns)

    def test_a_supplied_vwap_is_kept(self):
        df = _frame(_rising(60))
        df["vwap"] = 42.0

        result = _indicators(df)

        self.assertTrue((result["vwap"] == 42.0).all())

    def test_vwap_is_derived_when_absent(self):
        result = _indicators(_frame(_rising(60)))

        self.assertIn("vwap", result.columns)
        self.assertFalse(result["vwap"].isna().all())

    def test_the_requested_timeframe_selects_the_lookback(self):
        provider = mock.MagicMock()
        provider.get_bars.return_value = _frame(_rising(60))
        with mock.patch(
            "tradingagents.marketdata.get_research_market_data_provider",
            lambda *a, **k: provider,
        ):
            tb.compute_indicators("NVDA", "2026-06-01", "4h")

        self.assertEqual(provider.get_bars.call_args.kwargs["timeframe"], "4Hour")


class TrendDetectionTests(unittest.TestCase):
    def test_a_sustained_advance_reads_bullish(self):
        trend = tb.detect_trend(_indicators(_frame(_rising(150))))

        self.assertEqual(trend.direction, Direction.BULLISH)
        self.assertGreater(trend.ema_slope, 0)

    def test_a_sustained_decline_reads_bearish(self):
        trend = tb.detect_trend(_indicators(_frame(_falling(150))))

        self.assertEqual(trend.direction, Direction.BEARISH)
        self.assertLess(trend.ema_slope, 0)

    def test_a_directionless_series_is_weak(self):
        trend = tb.detect_trend(_indicators(_frame(_flat(150))))

        self.assertEqual(trend.strength, Strength.WEAK)

    def test_a_steep_advance_is_strong(self):
        trend = tb.detect_trend(_indicators(_frame(_rising(150, step=5.0))))

        self.assertEqual(trend.strength, Strength.STRONG)

    def test_distance_from_the_200_sma_is_positive_in_an_uptrend(self):
        trend = tb.detect_trend(_indicators(_frame(_rising(250))))

        self.assertGreater(trend.sma_200, 0)
        self.assertGreater(trend.sma_200_dist, 0)

    def test_a_short_history_reports_no_200_sma(self):
        """Below 200 bars the average is undefined and must not be invented."""
        trend = tb.detect_trend(_indicators(_frame(_rising(120))))

        self.assertEqual(trend.sma_200, 0.0)
        self.assertEqual(trend.sma_200_dist, 0.0)

    def test_the_adx_label_follows_its_thresholds(self):
        for value, expected in ((10.0, "weak"), (30.0, "strong"), (50.0, "very_strong")):
            df = _indicators(_frame(_rising(150)))
            df["adx_14"] = value

            self.assertEqual(tb.detect_trend(df).trend_strength_adx, expected, value)

    def test_swing_detection_needs_enough_bars(self):
        self.assertEqual(tb._detect_hh_hl(_frame(_rising(5))), (False, False))

    def test_rising_swings_are_reported_as_higher_highs_and_lows(self):
        """Each peak and trough is a strict five-bar pivot, stepping up."""
        highs, lows = [], []
        for cycle in range(4):
            base = 100 + cycle * 20
            highs.extend([base, base + 2, base + 12, base + 2, base])
            lows.extend([base - 2, base - 4, base - 14, base - 4, base - 2])
        closes = [(high + low) / 2 for high, low in zip(highs, lows)]
        df = _frame(closes, highs=highs, lows=lows)

        higher_highs, higher_lows = tb._detect_hh_hl(df, lookback=len(closes))

        self.assertTrue(higher_highs)
        self.assertTrue(higher_lows)


class MomentumDetectionTests(unittest.TestCase):
    def test_an_unbroken_advance_is_overbought(self):
        momentum = tb.detect_momentum(_indicators(_frame(_rising(150))))

        self.assertEqual(momentum.rsi_zone, "overbought")
        self.assertGreater(momentum.rsi_value, 70)

    def test_an_unbroken_decline_is_oversold(self):
        momentum = tb.detect_momentum(_indicators(_frame(_falling(150))))

        self.assertEqual(momentum.rsi_zone, "oversold")
        self.assertLess(momentum.rsi_value, 30)

    def test_a_directionless_series_is_neutral(self):
        momentum = tb.detect_momentum(_indicators(_frame(_flat(150))))

        self.assertEqual(momentum.rsi_zone, "neutral")

    def test_the_macd_cross_is_one_of_the_declared_values(self):
        momentum = tb.detect_momentum(_indicators(_frame(_rising(150))))

        self.assertIn(momentum.macd_cross, {"bullish", "bearish", "none"})

    def test_the_histogram_trend_is_one_of_the_declared_values(self):
        momentum = tb.detect_momentum(_indicators(_frame(_rising(150))))

        self.assertIn(momentum.macd_histogram_trend, {"expanding", "contracting", "flat"})

    def test_the_stochastic_state_is_bounded_and_labelled(self):
        momentum = tb.detect_momentum(_indicators(_frame(_rising(150))))

        self.assertGreaterEqual(momentum.stoch_k, 0)
        self.assertLessEqual(momentum.stoch_k, 100)
        self.assertIn(momentum.stoch_state, {"oversold", "neutral", "overbought"})


class VwapDetectionTests(unittest.TestCase):
    def test_price_above_the_vwap_reads_above(self):
        df = _indicators(_frame(_rising(150)))
        df.loc[df.index[-1], "vwap"] = df["close"].iloc[-1] * 0.5

        self.assertEqual(tb.detect_vwap_state(df).position, "above")

    def test_price_below_the_vwap_reads_below(self):
        df = _indicators(_frame(_rising(150)))
        df.loc[df.index[-1], "vwap"] = df["close"].iloc[-1] * 1.5

        self.assertEqual(tb.detect_vwap_state(df).position, "below")

    def test_a_missing_vwap_is_reported_as_at_with_no_distance(self):
        df = _indicators(_frame(_rising(150)))
        df.loc[df.index[-1], "vwap"] = np.nan

        state = tb.detect_vwap_state(df)

        self.assertEqual(state.position, "at")
        self.assertEqual(state.zscore_distance, 0.0)


class VolatilityDetectionTests(unittest.TestCase):
    def test_the_atr_percentile_is_a_percentage(self):
        volatility = tb.detect_volatility(_indicators(_frame(_rising(150))))

        self.assertGreaterEqual(volatility.atr_percentile, 0)
        self.assertLessEqual(volatility.atr_percentile, 100)

    def test_squeeze_and_breakout_are_mutually_exclusive(self):
        for closes in (_rising(150), _falling(150), _flat(150)):
            volatility = tb.detect_volatility(_indicators(_frame(closes)))

            self.assertFalse(volatility.squeeze and volatility.breakout)

    def test_the_gap_is_measured_against_the_previous_close(self):
        closes = _rising(150)
        opens = list(closes)
        opens[-1] = closes[-2] * 1.10
        df = _indicators(_frame(closes, opens=opens))

        self.assertAlmostEqual(tb.detect_volatility(df).gap_percent, 10.0, places=1)


class VolumeDetectionTests(unittest.TestCase):
    def test_a_volume_spike_lifts_the_ratio_above_one(self):
        volumes = [1_000.0] * 149 + [10_000.0]
        df = _indicators(_frame(_rising(150), volumes=volumes))

        self.assertGreater(tb.detect_volume(df).vol_ma_ratio, 1.0)

    def test_growing_volume_reads_as_rising(self):
        volumes = [1_000.0 * (1.05 ** index) for index in range(150)]
        df = _indicators(_frame(_rising(150), volumes=volumes))

        self.assertEqual(tb.detect_volume(df).vol_trend, "up")

    def test_shrinking_volume_reads_as_falling(self):
        volumes = [1_000.0 * (0.95 ** index) for index in range(150)]
        df = _indicators(_frame(_rising(150), volumes=volumes))

        self.assertEqual(tb.detect_volume(df).vol_trend, "down")

    def test_steady_volume_reads_as_flat(self):
        df = _indicators(_frame(_rising(150)))

        self.assertEqual(tb.detect_volume(df).vol_trend, "flat")


class MarketStructureTests(unittest.TestCase):
    def test_the_reported_swings_bracket_recent_price(self):
        structure = tb.detect_market_structure(_indicators(_frame(_rising(150))))

        self.assertGreaterEqual(structure.last_swing_high, structure.last_swing_low)

    def test_break_of_structure_and_change_of_character_are_booleans(self):
        structure = tb.detect_market_structure(_indicators(_frame(_flat(150))))

        self.assertIsInstance(structure.bos, bool)
        self.assertIsInstance(structure.choch, bool)


class LevelExtractionTests(unittest.TestCase):
    def test_levels_are_returned_for_a_normal_series(self):
        df = _indicators(_frame(_rising(150)))

        levels = tb.extract_key_levels({"1d": df})

        self.assertIsInstance(levels, list)
        for level in levels:
            self.assertIsInstance(level, KeyLevel)

    def test_near_identical_levels_collapse(self):
        levels = [
            KeyLevel(label="VWAP", price=100.0, type="support"),
            KeyLevel(label="Yesterday Low", price=100.05, type="support"),
            KeyLevel(label="Pivot R1", price=120.0, type="resistance"),
        ]

        deduplicated = tb._deduplicate_levels(levels, 100.0, tolerance_pct=1.0)

        self.assertEqual(len(deduplicated), 2)
        # The more descriptive label survives the collapse.
        self.assertIn("Yesterday Low", [level.label for level in deduplicated])

    def test_distinct_levels_survive_deduplication(self):
        levels = [
            KeyLevel(label="VWAP", price=100.0, type="support"),
            KeyLevel(label="Pivot R1", price=150.0, type="resistance"),
        ]

        self.assertEqual(len(tb._deduplicate_levels(levels, 100.0, tolerance_pct=1.0)), 2)

    def test_no_levels_deduplicates_to_nothing(self):
        self.assertEqual(tb._deduplicate_levels([], 100.0, tolerance_pct=1.0), [])


def _brief(direction, *, rsi_zone="neutral", squeeze=False, breakout=False,
           bos=False, macd_cross="none", timeframe="1h"):
    return TimeframeBrief(
        timeframe=timeframe,
        trend=TrendState(
            direction=direction,
            strength=Strength.MODERATE,
            ema_slope=1.0,
            higher_highs=False,
            higher_lows=False,
        ),
        momentum=MomentumState(
            rsi_value=50.0,
            rsi_zone=rsi_zone,
            rsi_divergence=False,
            macd_cross=macd_cross,
            macd_histogram_trend="flat",
        ),
        vwap_state=VWAPState(position="at", zscore_distance=0.0),
        volatility=VolatilityState(
            atr_value=1.0, atr_percentile=50.0, squeeze=squeeze, breakout=breakout
        ),
        volume=VolumeState(vol_ma_ratio=1.0, vol_trend="flat", obv_slope=0.0),
        market_structure=MarketStructure(
            bos=bos, choch=False, last_swing_high=110.0, last_swing_low=90.0
        ),
    )


class SignalSummaryTests(unittest.TestCase):
    def test_no_timeframes_is_reported_as_insufficient(self):
        summary = tb.generate_signal_summary([], [])

        self.assertEqual(summary.setup, "none")
        self.assertEqual(summary.confidence, "low")
        self.assertIn("Insufficient", summary.description)

    def test_agreeing_bullish_timeframes_read_as_continuation(self):
        briefs = [_brief(Direction.BULLISH) for _ in range(3)]

        summary = tb.generate_signal_summary(briefs, [])

        self.assertEqual(summary.setup, "trend_continuation")
        self.assertIn("bullish", summary.description)

    def test_agreeing_bearish_timeframes_read_as_continuation(self):
        briefs = [_brief(Direction.BEARISH) for _ in range(3)]

        summary = tb.generate_signal_summary(briefs, [])

        self.assertEqual(summary.setup, "trend_continuation")
        self.assertIn("bearish", summary.description)

    def test_a_squeeze_is_flagged_as_a_breakout_setup(self):
        briefs = [_brief(Direction.NEUTRAL, squeeze=True)] + [
            _brief(Direction.NEUTRAL) for _ in range(2)
        ]

        self.assertEqual(tb.generate_signal_summary(briefs, []).setup, "breakout")

    def test_a_breakout_with_structure_confirmation_is_a_breakout(self):
        briefs = [_brief(Direction.BULLISH, breakout=True, bos=True)] + [
            _brief(Direction.BULLISH) for _ in range(2)
        ]

        summary = tb.generate_signal_summary(briefs, [])

        self.assertEqual(summary.setup, "breakout")
        self.assertIn("BOS", summary.description)

    def test_a_bullish_trend_with_oversold_momentum_is_mean_reversion(self):
        briefs = [_brief(Direction.BULLISH, rsi_zone="oversold")] + [
            _brief(Direction.BULLISH) for _ in range(2)
        ]

        self.assertEqual(tb.generate_signal_summary(briefs, []).setup, "mean_reversion")

    def test_a_bearish_trend_with_overbought_momentum_is_mean_reversion(self):
        briefs = [_brief(Direction.BEARISH, rsi_zone="overbought")] + [
            _brief(Direction.BEARISH) for _ in range(2)
        ]

        self.assertEqual(tb.generate_signal_summary(briefs, []).setup, "mean_reversion")

    def test_a_short_term_counter_move_is_a_pullback(self):
        briefs = [
            _brief(Direction.NEUTRAL, timeframe="1h"),
            _brief(Direction.BULLISH, timeframe="4h"),
            _brief(Direction.BULLISH, timeframe="1d"),
        ]

        summary = tb.generate_signal_summary(briefs, [])

        self.assertEqual(summary.setup, "pullback")

    def test_full_alignment_with_macd_confirmation_is_high_confidence(self):
        briefs = [_brief(Direction.BULLISH, macd_cross="bullish") for _ in range(3)]

        self.assertEqual(tb.generate_signal_summary(briefs, []).confidence, "high")

    def test_a_majority_without_confirmation_is_medium_confidence(self):
        briefs = [
            _brief(Direction.BULLISH),
            _brief(Direction.BULLISH),
            _brief(Direction.BEARISH),
        ]

        self.assertEqual(tb.generate_signal_summary(briefs, []).confidence, "medium")

    def test_disagreement_is_low_confidence(self):
        briefs = [
            _brief(Direction.BULLISH),
            _brief(Direction.BEARISH),
            _brief(Direction.NEUTRAL),
        ]

        summary = tb.generate_signal_summary(briefs, [])

        self.assertEqual(summary.confidence, "low")
        self.assertEqual(summary.setup, "none")


class BuildTechnicalBriefTests(unittest.TestCase):
    def test_the_brief_covers_every_timeframe(self):
        provider = mock.MagicMock()
        provider.get_bars.return_value = _frame(_rising(250))
        with mock.patch(
            "tradingagents.marketdata.get_research_market_data_provider",
            lambda *a, **k: provider,
        ):
            brief = tb.build_technical_brief("NVDA", "2026-06-01")

        self.assertEqual(brief.symbol, "NVDA")
        self.assertEqual(
            [item.timeframe for item in brief.timeframes], ["1h", "4h", "1d"]
        )
        self.assertIsInstance(brief.signal_summary, SignalSummary)

    def test_a_brief_survives_a_provider_with_no_data(self):
        """One dead timeframe must not take the whole brief down."""
        provider = mock.MagicMock()
        provider.get_bars.return_value = pd.DataFrame()
        with mock.patch(
            "tradingagents.marketdata.get_research_market_data_provider",
            lambda *a, **k: provider,
        ):
            brief = tb.build_technical_brief("NVDA", "2026-06-01")

        self.assertEqual(brief.timeframes, [])
        self.assertEqual(brief.signal_summary.setup, "none")

    def test_the_brief_serializes_to_json(self):
        """It is the fixed contract handed to the market analyst."""
        provider = mock.MagicMock()
        provider.get_bars.return_value = _frame(_rising(250))
        with mock.patch(
            "tradingagents.marketdata.get_research_market_data_provider",
            lambda *a, **k: provider,
        ):
            brief = tb.build_technical_brief("NVDA", "2026-06-01")

        payload = brief.model_dump_json()

        self.assertIn("NVDA", payload)
        self.assertIn("signal_summary", payload)


if __name__ == "__main__":
    unittest.main()
