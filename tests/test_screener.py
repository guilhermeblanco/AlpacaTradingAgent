import os
import unittest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from tradingagents.screener import (
    compute_signals,
    score_candidate,
    apply_filters,
    load_universe,
    SCREENER_MIN_SCORE_STOCK,
    SCREENER_MIN_SCORE_CRYPTO,
)


class TestScreener(unittest.TestCase):

    def setUp(self):
        # Generate dummy 40-day OHLCV data for testing
        dates = pd.date_range(end=datetime.now(), periods=40, freq='B')
        np.random.seed(42)
        close_prices = 100.0 + np.cumsum(np.random.randn(40))
        high_prices = close_prices + np.abs(np.random.randn(40))
        low_prices = close_prices - np.abs(np.random.randn(40))
        open_prices = close_prices + np.random.randn(40) * 0.5
        volume = np.random.randint(600000, 2000000, size=40)

        self.df_normal = pd.DataFrame({
            'Open': open_prices,
            'High': high_prices,
            'Low': low_prices,
            'Close': close_prices,
            'Volume': volume
        }, index=dates)

        # Generate a bullish volume spike + gap dataframe
        df_bullish = self.df_normal.copy()
        df_bullish.iloc[-1, df_bullish.columns.get_loc('Volume')] = 5000000  # Massive volume spike (5x)
        df_bullish.iloc[-1, df_bullish.columns.get_loc('Open')] = df_bullish.iloc[-2]['Close'] * 1.05  # 5% Gap up
        df_bullish.iloc[-1, df_bullish.columns.get_loc('Close')] = df_bullish.iloc[-2]['Close'] * 1.06
        self.df_bullish = df_bullish

    def test_compute_signals_insufficient_data(self):
        short_df = self.df_normal.iloc[:10]
        signals = compute_signals(short_df, "TEST")
        self.assertIsNone(signals)

    def test_compute_signals_valid(self):
        signals = compute_signals(self.df_normal, "TEST")
        self.assertIsNotNone(signals)
        self.assertEqual(signals['symbol'], "TEST")
        self.assertIn('volume_spike', signals)
        self.assertIn('rsi_14', signals)
        self.assertIn('sma50_reclaim', signals)
        self.assertIn('bb_squeeze', signals)
        self.assertIn('gap_pct', signals)

    def test_score_candidate(self):
        signals = compute_signals(self.df_bullish, "BULL")
        score = score_candidate(signals)
        self.assertIsInstance(score, float)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 10.0)
        # Bullish df should score points for volume spike and gap
        self.assertGreaterEqual(score, 2.0)

    def test_apply_filters(self):
        candidates = [
            {
                'symbol': 'AAPL',
                'asset_type': 'stock',
                'score': 8.5,
                'signals': {'avg_volume': 1000000}
            },
            {
                'symbol': 'LOW_SCORE',
                'asset_type': 'stock',
                'score': 3.0,
                'signals': {'avg_volume': 1000000}
            },
            {
                'symbol': 'OWNED_SYM',
                'asset_type': 'stock',
                'score': 9.0,
                'signals': {'avg_volume': 1000000}
            },
            {
                'symbol': 'BTC-USD',
                'asset_type': 'crypto',
                'score': 5.0,  # Above crypto min score (4.0)
                'signals': {'avg_volume': 50000}
            }
        ]

        owned = {'OWNED_SYM'}
        pending = set()
        cooldown = {}

        filtered = apply_filters(
            candidates,
            owned_symbols=owned,
            pending_symbols=pending,
            cooldown_map=cooldown,
            asset_filter='all'
        )

        filtered_symbols = [c['symbol'] for c in filtered]
        self.assertIn('AAPL', filtered_symbols)
        self.assertIn('BTC-USD', filtered_symbols)
        self.assertNotIn('LOW_SCORE', filtered_symbols)  # Score 3.0 < 7.0
        self.assertNotIn('OWNED_SYM', filtered_symbols)  # Owned

    def test_load_universe(self):
        universe = load_universe()
        self.assertIsInstance(universe, dict)
        self.assertGreater(len(universe), 0)


if __name__ == '__main__':
    unittest.main()
