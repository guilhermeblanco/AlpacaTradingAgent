"""Tests for ticker format normalization.

One symbol reaches five vendors in five spellings — Alpaca wants BTC/USD,
Yahoo wants BTC-USD, CoinDesk wants BTC. Get the crypto detection wrong and
a stock request goes out as a currency pair, which fails silently as "no
data" rather than as an error.
"""

from __future__ import annotations

import unittest

from tradingagents.dataflows.ticker_utils import (
    TickerUtils,
    format_for_alpaca,
    format_for_openai_news,
    get_base_crypto_symbol,
    is_crypto_ticker,
    normalize_ticker_for_logs,
)


class CryptoDetectionTests(unittest.TestCase):
    def test_an_explicit_pair_is_crypto_in_either_separator(self):
        for ticker in ("BTC/USD", "BTC-USD", "ETH/USDT", "SOL-USDC"):
            self.assertTrue(is_crypto_ticker(ticker), ticker)

    def test_a_crosscurrency_pair_is_crypto(self):
        self.assertTrue(is_crypto_ticker("ETH/BTC"))

    def test_a_bare_known_symbol_is_crypto(self):
        self.assertTrue(is_crypto_ticker("BTC"))
        self.assertTrue(is_crypto_ticker("doge"))

    def test_a_concatenated_pair_is_crypto(self):
        self.assertTrue(is_crypto_ticker("BTCUSD"))
        self.assertTrue(is_crypto_ticker("ETHUSDT"))

    def test_an_ordinary_stock_is_not_crypto(self):
        for ticker in ("AAPL", "NVDA", "BRK-B", "SPY"):
            self.assertFalse(is_crypto_ticker(ticker), ticker)

    def test_nothing_is_not_crypto(self):
        self.assertFalse(is_crypto_ticker(""))


class BaseSymbolTests(unittest.TestCase):
    def test_the_base_is_taken_from_either_separator(self):
        self.assertEqual(get_base_crypto_symbol("BTC/USD"), "BTC")
        self.assertEqual(get_base_crypto_symbol("BTC-USD"), "BTC")

    def test_a_concatenated_quote_currency_is_stripped(self):
        self.assertEqual(get_base_crypto_symbol("BTCUSD"), "BTC")
        self.assertEqual(get_base_crypto_symbol("BTCUSDT"), "BTC")
        self.assertEqual(get_base_crypto_symbol("BTCUSDC"), "BTC")

    def test_a_bare_symbol_is_already_the_base(self):
        self.assertEqual(get_base_crypto_symbol("eth"), "ETH")


class StandardizationTests(unittest.TestCase):
    def test_every_crypto_spelling_normalizes_to_the_same_set(self):
        forms = [
            TickerUtils.standardize_ticker(t)
            for t in ("BTC/USD", "btc-usd", "BTCUSD", " BTC ")
        ]

        for form in forms:
            self.assertTrue(form["is_crypto"])
            self.assertEqual(form["base_symbol"], "BTC")
            self.assertEqual(form["alpaca_format"], "BTC/USD")
            self.assertEqual(form["yahoo_format"], "BTC-USD")
            self.assertEqual(form["openai_format"], "BTCUSD")
            self.assertEqual(form["coindesk_format"], "BTC")

    def test_a_stock_uses_the_same_spelling_everywhere(self):
        form = TickerUtils.standardize_ticker("aapl")

        self.assertFalse(form["is_crypto"])
        self.assertEqual(
            {
                form["alpaca_format"],
                form["yahoo_format"],
                form["openai_format"],
                form["coindesk_format"],
                form["clean_symbol"],
            },
            {"AAPL"},
        )

    def test_punctuation_is_stripped_from_a_stock_symbol(self):
        self.assertEqual(
            TickerUtils.standardize_ticker("BRK-B")["clean_symbol"], "BRKB"
        )

    def test_the_original_input_is_preserved_upper_cased(self):
        self.assertEqual(TickerUtils.standardize_ticker(" btc/usd ")["original"], "BTC/USD")

    def test_an_empty_ticker_is_refused(self):
        with self.assertRaises(ValueError):
            TickerUtils.standardize_ticker("")


class ApiConversionTests(unittest.TestCase):
    def test_each_vendor_gets_its_own_spelling(self):
        self.assertEqual(TickerUtils.convert_for_api("BTC/USD", "alpaca"), "BTC/USD")
        self.assertEqual(TickerUtils.convert_for_api("BTC/USD", "yahoo"), "BTC-USD")
        self.assertEqual(TickerUtils.convert_for_api("BTC/USD", "openai"), "BTCUSD")
        self.assertEqual(TickerUtils.convert_for_api("BTC/USD", "coindesk"), "BTC")
        self.assertEqual(TickerUtils.convert_for_api("BTC/USD", "clean"), "BTC")
        self.assertEqual(TickerUtils.convert_for_api("BTC/USD", "display"), "BTC/USD")

    def test_the_vendor_name_is_matched_case_insensitively(self):
        self.assertEqual(TickerUtils.convert_for_api("BTC/USD", "Alpaca"), "BTC/USD")

    def test_an_unknown_vendor_gets_the_input_back(self):
        self.assertEqual(TickerUtils.convert_for_api("BTC/USD", "bloomberg"), "BTC/USD")

    def test_the_convenience_wrappers_agree_with_the_general_one(self):
        self.assertEqual(format_for_alpaca("btcusd"), "BTC/USD")
        self.assertEqual(format_for_openai_news("btcusd"), "BTCUSD")


class SymbolInfoTests(unittest.TestCase):
    def test_crypto_is_labelled_as_such(self):
        info = TickerUtils.get_symbol_info("BTC/USD")

        self.assertEqual(info["symbol_type"], "cryptocurrency")
        self.assertIn("BTC/USD", info["description"])

    def test_a_stock_is_labelled_as_such(self):
        info = TickerUtils.get_symbol_info("AAPL")

        self.assertEqual(info["symbol_type"], "stock")
        self.assertIn("Stock symbol", info["description"])

    def test_the_format_fields_are_carried_through(self):
        info = TickerUtils.get_symbol_info("BTC/USD")

        self.assertEqual(info["alpaca_format"], "BTC/USD")


class LogNormalizationTests(unittest.TestCase):
    def test_a_pair_logs_in_display_form(self):
        self.assertEqual(normalize_ticker_for_logs("btcusd"), "BTC/USD")

    def test_an_unusable_ticker_logs_as_given(self):
        """Logging must never be the thing that raises."""
        self.assertEqual(normalize_ticker_for_logs(""), "")
        self.assertIsNone(normalize_ticker_for_logs(None))


if __name__ == "__main__":
    unittest.main()
