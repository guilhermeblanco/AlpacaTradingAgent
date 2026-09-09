"""Tests for the Alpaca boundary.

Two very different contracts live here. Read paths feed prompts and panels,
so they degrade to an empty result on an outage. Execution paths must not:
a guessed position turns a BUY into pyramiding an existing holding and a
SELL into a skipped exit, which is why they take ``strict=True``.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

import pandas as pd

from tradingagents.dataflows import alpaca_utils
from tradingagents.dataflows.alpaca_utils import AlpacaUtils


def _position(symbol, qty):
    return SimpleNamespace(symbol=symbol, qty=qty)


def _client(positions=None, error=None):
    client = mock.MagicMock()
    if error is not None:
        client.get_all_positions.side_effect = error
    else:
        client.get_all_positions.return_value = positions or []
    return mock.patch.object(
        alpaca_utils, "get_alpaca_trading_client", lambda: client
    )


class PositionStateTests(unittest.TestCase):
    def test_a_long_holding_reads_as_long(self):
        with _client([_position("NVDA", "10")]):
            self.assertEqual(AlpacaUtils.get_current_position_state("NVDA"), "LONG")

    def test_a_short_holding_reads_as_short(self):
        with _client([_position("NVDA", "-10")]):
            self.assertEqual(AlpacaUtils.get_current_position_state("NVDA"), "SHORT")

    def test_no_holding_reads_as_neutral(self):
        with _client([]):
            self.assertEqual(AlpacaUtils.get_current_position_state("NVDA"), "NEUTRAL")

    def test_a_zero_quantity_reads_as_neutral(self):
        with _client([_position("NVDA", "0")]):
            self.assertEqual(AlpacaUtils.get_current_position_state("NVDA"), "NEUTRAL")

    def test_a_crypto_pair_matches_the_broker_symbol(self):
        """Alpaca reports BTC/USD positions as BTCUSD."""
        with _client([_position("BTCUSD", "1.5")]):
            self.assertEqual(
                AlpacaUtils.get_current_position_state("BTC/USD"), "LONG"
            )

    def test_an_outage_reads_as_neutral_for_prompts(self):
        with _client(error=RuntimeError("broker unreachable")):
            self.assertEqual(AlpacaUtils.get_current_position_state("NVDA"), "NEUTRAL")

    def test_an_outage_is_raised_for_execution(self):
        """A guessed NEUTRAL would re-buy a real holding or skip a real exit."""
        with _client(error=RuntimeError("broker unreachable")):
            with self.assertRaises(RuntimeError):
                AlpacaUtils.get_current_position_state("NVDA", strict=True)

    def test_a_corrupted_quantity_reads_as_neutral_for_prompts(self):
        with _client([_position("NVDA", "not-a-number")]):
            self.assertEqual(AlpacaUtils.get_current_position_state("NVDA"), "NEUTRAL")

    def test_a_corrupted_quantity_is_raised_for_execution(self):
        with _client([_position("NVDA", "not-a-number")]):
            with self.assertRaises(Exception):
                AlpacaUtils.get_current_position_state("NVDA", strict=True)

    def test_a_non_finite_quantity_reads_as_neutral_for_prompts(self):
        """Outage artifacts arrive as 'nan', which parses without raising."""
        with _client([_position("NVDA", "nan")]):
            self.assertEqual(AlpacaUtils.get_current_position_state("NVDA"), "NEUTRAL")

    def test_a_non_finite_quantity_is_raised_for_execution(self):
        with _client([_position("NVDA", "nan")]):
            with self.assertRaises(ValueError):
                AlpacaUtils.get_current_position_state("NVDA", strict=True)

    def test_another_symbols_position_is_ignored(self):
        with _client([_position("AAPL", "10")]):
            self.assertEqual(AlpacaUtils.get_current_position_state("NVDA"), "NEUTRAL")


class AssetSearchTests(unittest.TestCase):
    ASSETS = [
        {"symbol": "NVDA", "name": "NVIDIA CORP", "asset_class": "us_equity", "tradable": True},
        {"symbol": "NVDL", "name": "GRANITESHARES NVDA", "asset_class": "us_equity", "tradable": True},
        {"symbol": "AAPL", "name": "APPLE INC", "asset_class": "us_equity", "tradable": True},
        {"symbol": "BTC/USD", "name": "BITCOIN", "asset_class": "crypto", "tradable": True},
        {"symbol": "BTC/USDT", "name": "BITCOIN TETHER", "asset_class": "crypto", "tradable": True},
    ]

    def _assets(self):
        return mock.patch.object(
            AlpacaUtils, "_get_searchable_assets", staticmethod(lambda *a, **k: self.ASSETS)
        )

    def test_an_exact_symbol_ranks_first(self):
        with self._assets():
            results = AlpacaUtils.search_assets("NVDA")

        self.assertEqual(results[0]["symbol"], "NVDA")

    def test_a_prefix_matches_related_symbols(self):
        with self._assets():
            symbols = [item["symbol"] for item in AlpacaUtils.search_assets("NVD")]

        self.assertIn("NVDA", symbols)
        self.assertIn("NVDL", symbols)

    def test_a_company_name_matches(self):
        with self._assets():
            symbols = [item["symbol"] for item in AlpacaUtils.search_assets("APPLE")]

        self.assertIn("AAPL", symbols)

    def test_a_crypto_base_prefers_the_usd_pair(self):
        with self._assets():
            results = AlpacaUtils.search_assets("BTC")

        self.assertEqual(results[0]["symbol"], "BTC/USD")

    def test_a_slashed_query_still_matches(self):
        with self._assets():
            symbols = [item["symbol"] for item in AlpacaUtils.search_assets("BTC/USD")]

        self.assertIn("BTC/USD", symbols)

    def test_an_unmatched_query_returns_nothing(self):
        with self._assets():
            self.assertEqual(AlpacaUtils.search_assets("ZZZZZZ"), [])

    def test_the_limit_is_honoured(self):
        with self._assets():
            self.assertLessEqual(len(AlpacaUtils.search_assets("B", limit=1)), 1)

    def test_an_empty_query_offers_defaults(self):
        with self._assets():
            results = AlpacaUtils.search_assets("")

        self.assertTrue(results)

    def test_matching_ignores_case(self):
        with self._assets():
            self.assertEqual(AlpacaUtils.search_assets("nvda")[0]["symbol"], "NVDA")


class AssetCacheTests(unittest.TestCase):
    def setUp(self):
        alpaca_utils._ASSET_SEARCH_CACHE.clear()
        self.addCleanup(alpaca_utils._ASSET_SEARCH_CACHE.clear)

    def test_a_broker_outage_falls_back_to_a_known_symbol_list(self):
        """The symbol picker must still work without credentials."""
        with mock.patch.object(
            alpaca_utils,
            "get_alpaca_trading_client",
            side_effect=RuntimeError("no credentials"),
        ):
            assets = AlpacaUtils._get_searchable_assets()

        self.assertTrue(assets)
        self.assertTrue(all("symbol" in asset for asset in assets))

    def test_the_fallback_is_cached_briefly_rather_than_refetched(self):
        calls = {"count": 0}

        def explode():
            calls["count"] += 1
            raise RuntimeError("no credentials")

        with mock.patch.object(alpaca_utils, "get_alpaca_trading_client", explode):
            AlpacaUtils._get_searchable_assets()
            AlpacaUtils._get_searchable_assets()

        self.assertEqual(calls["count"], 1)


class ReadPathTests(unittest.TestCase):
    def test_positions_degrade_to_an_empty_list(self):
        with mock.patch.object(
            alpaca_utils,
            "get_alpaca_trading_client",
            side_effect=RuntimeError("no credentials"),
        ):
            self.assertEqual(AlpacaUtils.get_positions_data(), [])

    def test_account_info_degrades_without_raising(self):
        with mock.patch.object(
            alpaca_utils,
            "get_alpaca_trading_client",
            side_effect=RuntimeError("no credentials"),
        ):
            info = AlpacaUtils.get_account_info()

        self.assertIsInstance(info, dict)

    def test_recent_orders_degrade_to_an_empty_page(self):
        with mock.patch.object(
            alpaca_utils,
            "get_alpaca_trading_client",
            side_effect=RuntimeError("no credentials"),
        ):
            page = AlpacaUtils.get_recent_orders_page(page=1, page_size=5)

        self.assertEqual(page["orders"], [])
        self.assertEqual(page["page"], 1)
        self.assertGreaterEqual(page["total_pages"], 1)

    def test_a_crypto_symbol_is_its_own_company_name(self):
        self.assertEqual(AlpacaUtils.get_company_name("BTC/USD"), "BTC/USD")

    def test_an_unknown_symbol_falls_back_to_itself(self):
        with mock.patch.object(
            alpaca_utils,
            "get_alpaca_trading_client",
            side_effect=RuntimeError("no credentials"),
        ):
            self.assertEqual(AlpacaUtils.get_company_name("ZZZZ"), "ZZZZ")

    def test_a_latest_quote_degrades_without_raising(self):
        client = mock.MagicMock()
        client.get_stock_latest_quote.side_effect = RuntimeError("no data")

        with mock.patch.object(
            alpaca_utils, "get_alpaca_stock_client", lambda: client
        ):
            quote = AlpacaUtils.get_latest_quote("NVDA")

        self.assertIsInstance(quote, dict)

    def test_stock_data_degrades_to_an_empty_frame(self):
        with mock.patch.object(
            alpaca_utils,
            "get_alpaca_stock_client",
            side_effect=RuntimeError("no credentials"),
        ):
            frame = AlpacaUtils.get_stock_data("NVDA", "2026-01-01", "2026-06-01")

        self.assertIsInstance(frame, pd.DataFrame)
        self.assertTrue(frame.empty)

    def test_open_orders_degrade_to_an_empty_set(self):
        with mock.patch.object(
            alpaca_utils,
            "get_alpaca_trading_client",
            side_effect=RuntimeError("no credentials"),
        ):
            self.assertEqual(AlpacaUtils.get_open_orders(), set())

    def test_tradeable_assets_degrade_to_an_empty_mapping(self):
        with mock.patch.object(
            alpaca_utils,
            "get_alpaca_trading_client",
            side_effect=RuntimeError("no credentials"),
        ):
            self.assertEqual(AlpacaUtils.get_tradeable_assets(), {})


class OrderPagingTests(unittest.TestCase):
    def _orders(self, count):
        return [
            SimpleNamespace(
                symbol="NVDA",
                side=SimpleNamespace(value="buy"),
                qty="1",
                filled_qty="1",
                status=SimpleNamespace(value="filled"),
                order_type=SimpleNamespace(value="market"),
                type=SimpleNamespace(value="market"),
                filled_avg_price="100.0",
                submitted_at=None,
                id=str(index),
                client_order_id=f"order-{index}",
                legs=None,
            )
            for index in range(count)
        ]

    def test_a_page_reports_its_own_position_in_the_history(self):
        client = mock.MagicMock()
        client.get_orders.return_value = self._orders(12)

        with mock.patch.object(
            alpaca_utils, "get_alpaca_trading_client", lambda: client
        ):
            page = AlpacaUtils.get_recent_orders_page(page=2, page_size=5)

        self.assertEqual(page["page"], 2)
        self.assertEqual(page["total_orders"], 12)
        self.assertEqual(page["total_pages"], 3)
        self.assertEqual(len(page["orders"]), 5)

    def test_the_last_page_holds_the_remainder(self):
        client = mock.MagicMock()
        client.get_orders.return_value = self._orders(7)

        with mock.patch.object(
            alpaca_utils, "get_alpaca_trading_client", lambda: client
        ):
            page = AlpacaUtils.get_recent_orders_page(page=2, page_size=5)

        self.assertEqual(len(page["orders"]), 2)
        self.assertEqual(page["total_pages"], 2)

    def test_has_more_marks_a_truncated_history_not_a_next_page(self):
        """It renders as "500+ orders"; the Next button uses total_pages."""
        client = mock.MagicMock()
        client.get_orders.return_value = self._orders(10)

        with mock.patch.object(
            alpaca_utils, "get_alpaca_trading_client", lambda: client
        ):
            within = AlpacaUtils.get_recent_orders_page(page=1, page_size=5, max_orders=500)
            truncated = AlpacaUtils.get_recent_orders_page(page=1, page_size=5, max_orders=10)

        self.assertFalse(within["has_more"])
        self.assertTrue(truncated["has_more"])

    def test_every_row_carries_the_keys_the_renderer_indexes(self):
        client = mock.MagicMock()
        client.get_orders.return_value = self._orders(1)

        with mock.patch.object(
            alpaca_utils, "get_alpaca_trading_client", lambda: client
        ):
            page = AlpacaUtils.get_recent_orders_page(page=1, page_size=5)

        for key in ("Asset", "Side", "Status", "Order Type", "Avg. Fill Price"):
            self.assertIn(key, page["orders"][0], key)


if __name__ == "__main__":
    unittest.main()
