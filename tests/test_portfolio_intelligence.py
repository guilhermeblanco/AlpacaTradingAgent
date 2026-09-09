"""Tests for the deterministic portfolio-intelligence layer.

The layer sits above the per-symbol agent decisions and adjusts NEW
position sizes only: positive correlation with existing positions above a
threshold scales the size down, realized volatility above the target
scales it down (inverse-volatility / simplified risk parity), and a gross
exposure cap clips what remains. Factors never size a trade UP, missing
data never punishes, and only an explicit exposure clip may zero a trade.
"""

import unittest

import numpy as np
import pandas as pd

from tradingagents.portfolio import (
    PortfolioLimitsConfig,
    adjust_new_position_notional,
    assess_new_position,
    daily_returns,
    realized_daily_vol,
)


def _frame(closes, start="2025-01-06"):
    dates = pd.bdate_range(start=start, periods=len(closes))
    closes = list(closes)
    return pd.DataFrame(
        {
            "timestamp": dates,
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1000.0] * len(closes),
        }
    )


def _trending(n=80, drift=0.01, noise_seed=7, scale=0.001):
    rng = np.random.default_rng(noise_seed)
    returns = drift + rng.normal(0, scale, n)
    return list(100 * np.cumprod(1 + returns))


def _config(**overrides):
    return PortfolioLimitsConfig(**overrides)


class ReturnMathTests(unittest.TestCase):
    def test_daily_returns_shape(self):
        returns = daily_returns(_frame([100, 110, 99]))
        self.assertEqual(len(returns), 2)
        self.assertAlmostEqual(returns.iloc[0], 0.10)

    def test_realized_vol_of_constant_series_is_zero(self):
        vol = realized_daily_vol(daily_returns(_frame([100] * 30)))
        self.assertEqual(vol, 0.0)


class CorrelationPenaltyTests(unittest.TestCase):
    def test_highly_correlated_candidate_is_scaled_down(self):
        closes = _trending()
        # Same series shifted in level: correlation ~1.
        verdict = assess_new_position(
            symbol="MSFT",
            requested_notional=10_000.0,
            equity=100_000.0,
            open_positions={"AAPL": 20_000.0},
            price_history={"MSFT": _frame(closes), "AAPL": _frame([c * 2 for c in closes])},
            config=_config(vol_sizing_enabled=False),
        )
        self.assertLess(verdict.adjusted_notional, 10_000.0)
        self.assertAlmostEqual(
            verdict.adjusted_notional, 10_000.0 * verdict.config.correlated_size_factor
        )
        self.assertTrue(any("correlation" in r.lower() for r in verdict.reasons))

    def test_uncorrelated_candidate_keeps_full_size(self):
        rng = np.random.default_rng(3)
        a = list(100 * np.cumprod(1 + rng.normal(0, 0.01, 80)))
        b = list(100 * np.cumprod(1 + rng.normal(0, 0.01, 80)))
        verdict = assess_new_position(
            symbol="GLD",
            requested_notional=10_000.0,
            equity=100_000.0,
            open_positions={"AAPL": 20_000.0},
            price_history={"GLD": _frame(a), "AAPL": _frame(b)},
            config=_config(vol_sizing_enabled=False),
        )
        self.assertEqual(verdict.adjusted_notional, 10_000.0)

    def test_negative_correlation_is_not_punished(self):
        closes = _trending()
        inverse = [200 - c + 100 for c in closes]  # strongly negative corr
        verdict = assess_new_position(
            symbol="SH",
            requested_notional=10_000.0,
            equity=100_000.0,
            open_positions={"AAPL": 20_000.0},
            price_history={"SH": _frame(inverse), "AAPL": _frame(closes)},
            config=_config(vol_sizing_enabled=False),
        )
        self.assertEqual(verdict.adjusted_notional, 10_000.0)


class VolatilitySizingTests(unittest.TestCase):
    def test_high_vol_candidate_is_scaled_down(self):
        rng = np.random.default_rng(11)
        wild = list(100 * np.cumprod(1 + rng.normal(0, 0.06, 80)))  # ~6% daily vol
        verdict = assess_new_position(
            symbol="MEME",
            requested_notional=10_000.0,
            equity=100_000.0,
            open_positions={},
            price_history={"MEME": _frame(wild)},
            config=_config(target_daily_vol_pct=2.0),
        )
        self.assertLess(verdict.adjusted_notional, 10_000.0)
        self.assertTrue(any("volatility" in r.lower() for r in verdict.reasons))

    def test_calm_candidate_is_never_sized_up(self):
        calm = list(np.linspace(100, 101, 80))  # tiny vol
        verdict = assess_new_position(
            symbol="BOND",
            requested_notional=10_000.0,
            equity=100_000.0,
            open_positions={},
            price_history={"BOND": _frame(calm)},
            config=_config(target_daily_vol_pct=2.0),
        )
        self.assertEqual(verdict.adjusted_notional, 10_000.0)


class ExposureCapTests(unittest.TestCase):
    def test_gross_exposure_headroom_clips_notional(self):
        verdict = assess_new_position(
            symbol="NVDA",
            requested_notional=30_000.0,
            equity=100_000.0,
            open_positions={"AAPL": 80_000.0},
            price_history={},
            config=_config(max_gross_exposure_pct=100.0),
        )
        # Headroom is 100k - 80k = 20k.
        self.assertEqual(verdict.adjusted_notional, 20_000.0)
        self.assertTrue(any("exposure" in r.lower() for r in verdict.reasons))

    def test_no_headroom_zeroes_the_trade_with_reason(self):
        verdict = assess_new_position(
            symbol="NVDA",
            requested_notional=10_000.0,
            equity=100_000.0,
            open_positions={"AAPL": 100_000.0},
            price_history={},
            config=_config(max_gross_exposure_pct=100.0),
        )
        self.assertEqual(verdict.adjusted_notional, 0.0)
        self.assertFalse(verdict.allowed)


class RobustnessTests(unittest.TestCase):
    def test_missing_price_data_keeps_full_size_with_warning(self):
        verdict = assess_new_position(
            symbol="NEW",
            requested_notional=10_000.0,
            equity=100_000.0,
            open_positions={"AAPL": 10_000.0},
            price_history={},  # nothing available
            config=_config(),
        )
        self.assertEqual(verdict.adjusted_notional, 10_000.0)
        self.assertTrue(verdict.allowed)
        self.assertTrue(any("unavailable" in r.lower() for r in verdict.reasons))

    def test_min_size_factor_floors_combined_penalties(self):
        closes = _trending()
        rng = np.random.default_rng(11)
        wild = list(
            np.array(closes) * np.cumprod(1 + rng.normal(0, 0.06, len(closes)))
        )
        config = _config(min_size_factor=0.25, target_daily_vol_pct=0.5)
        verdict = assess_new_position(
            symbol="MEME",
            requested_notional=10_000.0,
            equity=1_000_000.0,
            open_positions={"AAPL": 10_000.0},
            price_history={"MEME": _frame(wild), "AAPL": _frame(closes)},
            config=config,
        )
        self.assertGreaterEqual(verdict.adjusted_notional, 2_500.0)

    def test_adjust_helper_only_touches_new_long_exposure(self):
        # SELL/HOLD and closes pass through untouched even with data present.
        for action in ("SELL", "HOLD", "NEUTRAL"):
            amount = adjust_new_position_notional(
                symbol="AAPL",
                action=action,
                requested_notional=5_000.0,
                gather_state=lambda: (100_000.0, {"MSFT": 50_000.0}, {}),
                config=_config(),
            )
            self.assertEqual(amount, 5_000.0)

    def test_adjust_helper_survives_gather_failure(self):
        def broken_gather():
            raise ConnectionError("broker down")

        amount = adjust_new_position_notional(
            symbol="AAPL",
            action="BUY",
            requested_notional=5_000.0,
            gather_state=broken_gather,
            config=_config(),
        )
        self.assertEqual(amount, 5_000.0)


class ConfigTests(unittest.TestCase):
    def test_default_config_exposes_portfolio_keys(self):
        from tradingagents.default_config import DEFAULT_CONFIG

        self.assertIn("portfolio_intelligence_enabled", DEFAULT_CONFIG)
        self.assertIn("portfolio_high_correlation", DEFAULT_CONFIG)
        self.assertIn("portfolio_max_gross_exposure_pct", DEFAULT_CONFIG)

    def test_from_config_reads_project_keys(self):
        config = PortfolioLimitsConfig.from_config(
            {
                "portfolio_high_correlation": 0.5,
                "portfolio_max_gross_exposure_pct": 80.0,
            }
        )
        self.assertEqual(config.high_correlation, 0.5)
        self.assertEqual(config.max_gross_exposure_pct, 80.0)


if __name__ == "__main__":
    unittest.main()


class ReturnInputTests(unittest.TestCase):
    def test_a_close_series_is_accepted_directly(self):
        series = pd.Series([100.0, 101.0, 102.0])

        self.assertEqual(len(daily_returns(series)), 2)

    def test_a_frame_without_a_timestamp_column_uses_its_index(self):
        frame = pd.DataFrame({"Close": [100.0, 101.0, 102.0]})

        self.assertEqual(len(daily_returns(frame)), 2)

    def test_column_case_does_not_matter(self):
        frame = pd.DataFrame({"CLOSE": [100.0, 101.0]})

        self.assertEqual(len(daily_returns(frame)), 1)

    def test_a_frame_without_closes_is_refused(self):
        with self.assertRaises(ValueError):
            daily_returns(pd.DataFrame({"open": [100.0, 101.0]}))

    def test_a_single_return_has_no_measurable_volatility(self):
        self.assertEqual(realized_daily_vol(pd.Series([0.01])), 0.0)
        self.assertEqual(realized_daily_vol(None), 0.0)

    def test_a_candidate_with_almost_no_history_is_skipped(self):
        verdict = assess_new_position(
            "NVDA",
            1_000.0,
            100_000.0,
            {},
            {"NVDA": _frame([100.0, 101.0])},
            config=_config(),
        )

        self.assertEqual(verdict.adjusted_notional, 1_000.0)
        self.assertIn("Price history unavailable", " ".join(verdict.reasons))

    def test_unparseable_price_history_is_skipped_rather_than_fatal(self):
        verdict = assess_new_position(
            "NVDA",
            1_000.0,
            100_000.0,
            {},
            {"NVDA": pd.DataFrame({"open": [1.0, 2.0, 3.0, 4.0]})},
            config=_config(),
        )

        self.assertEqual(verdict.adjusted_notional, 1_000.0)

    def test_a_position_in_the_candidate_itself_is_not_correlated_with_itself(self):
        history = {"NVDA": _frame(_trending())}

        verdict = assess_new_position(
            "NVDA", 1_000.0, 100_000.0, {"NVDA": 5_000.0}, history, config=_config()
        )

        self.assertEqual(verdict.correlations, {})

    def test_a_position_without_price_history_is_skipped_for_correlation(self):
        verdict = assess_new_position(
            "NVDA",
            1_000.0,
            100_000.0,
            {"MYSTERY": 5_000.0},
            {"NVDA": _frame(_trending())},
            config=_config(),
        )

        self.assertEqual(verdict.correlations, {})

    def test_without_equity_the_exposure_cap_is_skipped_and_said_so(self):
        verdict = assess_new_position(
            "NVDA", 1_000.0, None, {}, {"NVDA": _frame(_trending())}, config=_config()
        )

        self.assertEqual(verdict.adjusted_notional, 1_000.0)
        self.assertIn("equity unavailable", " ".join(verdict.reasons))

    def test_a_zero_request_is_not_allowed(self):
        verdict = assess_new_position(
            "NVDA", 0.0, 100_000.0, {}, {"NVDA": _frame(_trending())}, config=_config()
        )

        self.assertFalse(verdict.allowed)

    def test_a_negative_request_is_treated_as_nothing(self):
        verdict = assess_new_position(
            "NVDA", -500.0, 100_000.0, {}, {"NVDA": _frame(_trending())}, config=_config()
        )

        self.assertEqual(verdict.requested_notional, 0.0)


class AlpacaStateGatheringTests(unittest.TestCase):
    """The portfolio layer needs the whole book, not just the candidate."""

    def _gather(self, *, equity="100000.0", positions=(), bars=None, symbol="NVDA"):
        from types import SimpleNamespace
        from unittest import mock

        from tradingagents.portfolio import gather_portfolio_state_via_alpaca

        client = mock.MagicMock()
        client.get_account.return_value = SimpleNamespace(equity=equity)
        client.get_all_positions.return_value = [
            SimpleNamespace(symbol=sym, market_value=value) for sym, value in positions
        ]

        requested = []

        def get_stock_data(sym, start, end):
            requested.append(sym)
            if bars is not None and sym not in bars:
                raise RuntimeError("no data")
            return (bars or {}).get(sym, _frame(_trending()))

        with mock.patch(
            "tradingagents.dataflows.alpaca_utils.get_alpaca_trading_client",
            lambda: client,
        ):
            with mock.patch(
                "tradingagents.dataflows.alpaca_utils.AlpacaUtils.get_stock_data",
                get_stock_data,
            ):
                return gather_portfolio_state_via_alpaca(symbol), requested

    def test_equity_and_open_positions_come_back(self):
        (equity, positions, _history), _requested = self._gather(
            positions=[("aapl", "5000.0")]
        )

        self.assertEqual(equity, 100_000.0)
        self.assertEqual(positions, {"AAPL": 5_000.0})

    def test_a_short_position_is_counted_at_its_absolute_size(self):
        (_equity, positions, _history), _requested = self._gather(
            positions=[("AAPL", "-5000.0")]
        )

        self.assertEqual(positions["AAPL"], 5_000.0)

    def test_an_unreadable_equity_reads_as_unknown(self):
        (equity, _positions, _history), _requested = self._gather(equity="n/a")

        self.assertIsNone(equity)

    def test_an_unreadable_position_is_skipped(self):
        (_equity, positions, _history), _requested = self._gather(
            positions=[("AAPL", "n/a"), ("MSFT", "1000.0")]
        )

        self.assertEqual(positions, {"MSFT": 1_000.0})

    def test_history_is_fetched_for_the_candidate_and_every_holding(self):
        (_equity, _positions, history), requested = self._gather(
            positions=[("AAPL", "5000.0")]
        )

        self.assertEqual(sorted(requested), ["AAPL", "NVDA"])
        self.assertIn("NVDA", history)
        self.assertIn("AAPL", history)

    def test_a_symbol_with_no_bars_is_left_out_rather_than_failing(self):
        (_equity, _positions, history), _requested = self._gather(
            positions=[("AAPL", "5000.0")], bars={"NVDA": _frame(_trending())}
        )

        self.assertIn("NVDA", history)
        self.assertNotIn("AAPL", history)

    def test_a_pair_is_reachable_under_both_spellings(self):
        """The candidate arrives as BTC/USD; Alpaca bars key on BTCUSD."""
        (_equity, _positions, history), _requested = self._gather(symbol="BTC/USD")

        self.assertIn("BTCUSD", history)
        self.assertIn("BTC/USD", history)
