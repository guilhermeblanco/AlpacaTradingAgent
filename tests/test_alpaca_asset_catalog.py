"""Tests for the Alpaca asset catalog, symbol search, and account readers.

These back the WebUI symbol picker, the screener's universe, and the
positions/orders panels. Each one has to degrade to something usable when
the broker is unreachable — a raised exception here takes a whole panel or
an unattended scan down with it.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from alpaca.trading.enums import AssetClass

from tradingagents.dataflows import alpaca_utils
from tradingagents.dataflows.alpaca_utils import (
    AlpacaUtils,
    _asset_to_search_result,
    _enum_value,
    _fallback_asset_results,
    _normalize_asset_symbol,
    _normalize_crypto_symbol,
    _parse_timeframe,
)


def _asset(symbol, *, asset_class=AssetClass.US_EQUITY, name="", exchange="NASDAQ",
           tradable=True, fractionable=True):
    return SimpleNamespace(
        symbol=symbol,
        asset_class=asset_class,
        name=name,
        exchange=exchange,
        tradable=tradable,
        fractionable=fractionable,
    )


class Client:
    def __init__(self, assets=(), orders=(), positions=(), error=None):
        self._assets = list(assets)
        self._orders = list(orders)
        self._positions = list(positions)
        self._error = error
        self.requests = []

    def _check(self):
        if self._error:
            raise self._error

    def get_all_assets(self, request=None):
        self.requests.append(request)
        self._check()
        wanted = getattr(request, "asset_class", None)
        if wanted is None:
            return list(self._assets)
        return [a for a in self._assets if a.asset_class == wanted]

    def get_asset(self, symbol):
        self._check()
        return next((a for a in self._assets if a.symbol == symbol), None)

    def get_orders(self, request=None):
        self._check()
        return list(self._orders)

    def get_all_positions(self):
        self._check()
        return list(self._positions)


class BrokerFixture(unittest.TestCase):
    def setUp(self):
        alpaca_utils._ASSET_SEARCH_CACHE.update({"expires_at": 0.0, "assets": []})
        AlpacaUtils._tradeable_assets_cache.update({"expires_at": 0.0, "assets": {}})
        self.addCleanup(
            lambda: alpaca_utils._ASSET_SEARCH_CACHE.update(
                {"expires_at": 0.0, "assets": []}
            )
        )
        self.addCleanup(
            lambda: AlpacaUtils._tradeable_assets_cache.update(
                {"expires_at": 0.0, "assets": {}}
            )
        )

    def _client(self, client):
        patcher = mock.patch.object(
            alpaca_utils, "get_alpaca_trading_client", lambda: client
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return client


class SymbolNormalizationTests(unittest.TestCase):
    def test_an_enum_yields_its_value(self):
        self.assertEqual(_enum_value(AssetClass.CRYPTO), "crypto")

    def test_a_plain_string_passes_through(self):
        self.assertEqual(_enum_value("NASDAQ"), "NASDAQ")

    def test_nothing_yields_an_empty_string(self):
        self.assertEqual(_enum_value(None), "")

    def test_a_pair_keeps_its_separator(self):
        self.assertEqual(_normalize_crypto_symbol("BTC/USD"), "BTC/USD")

    def test_a_hyphenated_pair_is_converted(self):
        self.assertEqual(_normalize_crypto_symbol("btc-usd"), "BTC/USD")

    def test_a_concatenated_pair_gains_its_separator(self):
        self.assertEqual(_normalize_crypto_symbol("BTCUSD"), "BTC/USD")
        self.assertEqual(_normalize_crypto_symbol("ETHUSDT"), "ETH/USDT")
        self.assertEqual(_normalize_crypto_symbol("SOLUSDC"), "SOL/USDC")

    def test_a_bare_base_symbol_is_left_alone(self):
        self.assertEqual(_normalize_crypto_symbol("BTC"), "BTC")

    def test_equities_are_only_upper_cased(self):
        self.assertEqual(
            _normalize_asset_symbol("brk.b", AssetClass.US_EQUITY.value), "BRK.B"
        )

    def test_crypto_gets_the_pair_treatment(self):
        self.assertEqual(
            _normalize_asset_symbol("btcusd", AssetClass.CRYPTO.value), "BTC/USD"
        )


class TimeframeParsingTests(unittest.TestCase):
    def test_the_common_shorthands_are_understood(self):
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        cases = {
            "1Min": (1, TimeFrameUnit.Minute),
            "5Min": (5, TimeFrameUnit.Minute),
            "15min": (15, TimeFrameUnit.Minute),
            "1Hour": (1, TimeFrameUnit.Hour),
            "1h": (1, TimeFrameUnit.Hour),
            "4Hour": (4, TimeFrameUnit.Hour),
            "4h": (4, TimeFrameUnit.Hour),
            "1Day": (1, TimeFrameUnit.Day),
            "1d": (1, TimeFrameUnit.Day),
        }

        for text, (amount, unit) in cases.items():
            parsed = _parse_timeframe(text)

            self.assertEqual((parsed.amount_value, parsed.unit_value), (amount, unit), text)

    def test_surrounding_whitespace_is_ignored(self):
        from alpaca.data.timeframe import TimeFrameUnit

        self.assertEqual(_parse_timeframe("  5Min  ").unit_value, TimeFrameUnit.Minute)

    def test_an_existing_timeframe_passes_through(self):
        from alpaca.data.timeframe import TimeFrame

        given = TimeFrame.Hour

        self.assertIs(_parse_timeframe(given), given)

    def test_a_multi_day_timeframe_is_refused_by_the_vendor(self):
        """Alpaca only bars daily; the parser does not paper over that."""
        for text in ("2Day", "3d"):
            with self.assertRaises(ValueError):
                _parse_timeframe(text)

    def test_an_unrecognized_timeframe_falls_back_to_daily(self):
        from alpaca.data.timeframe import TimeFrameUnit

        self.assertEqual(_parse_timeframe("fortnightly").unit_value, TimeFrameUnit.Day)


class AssetConversionTests(unittest.TestCase):
    def test_an_equity_is_described_for_the_picker(self):
        result = _asset_to_search_result(_asset("NVDA", name="NVIDIA Corporation"))

        self.assertEqual(result["symbol"], "NVDA")
        self.assertEqual(result["name"], "NVIDIA Corporation")
        self.assertEqual(result["asset_type"], "Equity")
        self.assertTrue(result["tradable"])

    def test_a_crypto_pair_is_labelled_as_crypto(self):
        result = _asset_to_search_result(
            _asset("BTCUSD", asset_class=AssetClass.CRYPTO, name="Bitcoin")
        )

        self.assertEqual(result["symbol"], "BTC/USD")
        self.assertEqual(result["asset_type"], "Crypto")

    def test_a_nameless_asset_falls_back_to_the_known_company_list(self):
        result = _asset_to_search_result(_asset("AAPL", name=""))

        self.assertTrue(result["name"])

    def test_an_unknown_nameless_asset_falls_back_to_its_symbol(self):
        result = _asset_to_search_result(_asset("ZZZZ", name=""))

        self.assertEqual(result["name"], "ZZZZ")

    def test_the_curated_fallback_covers_both_asset_classes(self):
        results = _fallback_asset_results()

        self.assertIn("NVDA", [r["symbol"] for r in results])
        self.assertIn("BTC/USD", [r["symbol"] for r in results])
        self.assertTrue(all(r["tradable"] for r in results))


class SearchableAssetTests(BrokerFixture):
    ASSETS = (
        _asset("NVDA", name="NVIDIA Corporation"),
        _asset("AAPL", name="Apple Inc."),
        _asset("BTCUSD", asset_class=AssetClass.CRYPTO, name="Bitcoin"),
    )

    def test_both_asset_classes_are_requested(self):
        client = self._client(Client(self.ASSETS))

        AlpacaUtils._get_searchable_assets()

        self.assertEqual(
            [r.asset_class for r in client.requests],
            [AssetClass.US_EQUITY, AssetClass.CRYPTO],
        )

    def test_the_catalog_is_cached_between_calls(self):
        client = self._client(Client(self.ASSETS))

        AlpacaUtils._get_searchable_assets()
        AlpacaUtils._get_searchable_assets()

        self.assertEqual(len(client.requests), 2)

    def test_an_expired_cache_is_refetched(self):
        client = self._client(Client(self.ASSETS))

        AlpacaUtils._get_searchable_assets(cache_seconds=-1)
        AlpacaUtils._get_searchable_assets(cache_seconds=-1)

        self.assertEqual(len(client.requests), 4)

    def test_duplicate_symbols_are_collapsed(self):
        self._client(Client((_asset("NVDA"), _asset("NVDA"))))

        found = AlpacaUtils._get_searchable_assets()

        self.assertEqual([a["symbol"] for a in found], ["NVDA"])

    def test_an_unreachable_broker_falls_back_to_the_curated_list(self):
        """The picker has to render something."""
        self._client(Client(error=RuntimeError("credentials rejected")))

        found = AlpacaUtils._get_searchable_assets()

        self.assertIn("NVDA", [a["symbol"] for a in found])


class SymbolSearchTests(BrokerFixture):
    ASSETS = (
        _asset("NVDA", name="NVIDIA Corporation"),
        _asset("NVDL", name="GraniteShares NVDA ETF"),
        _asset("AAPL", name="Apple Inc."),
        _asset("HALTED", name="Halted Co", tradable=False),
        _asset("BTCUSD", asset_class=AssetClass.CRYPTO, name="Bitcoin"),
        _asset("BTCUSDT", asset_class=AssetClass.CRYPTO, name="Bitcoin Tether"),
    )

    def setUp(self):
        super().setUp()
        self._client(Client(self.ASSETS))

    def _symbols(self, query, **kwargs):
        return [a["symbol"] for a in AlpacaUtils.search_assets(query, **kwargs)]

    def test_an_exact_symbol_ranks_first(self):
        self.assertEqual(self._symbols("aapl")[0], "AAPL")

    def test_a_prefix_matches(self):
        self.assertIn("NVDL", self._symbols("nvd"))

    def test_an_exact_match_outranks_a_prefix_match(self):
        found = self._symbols("nvda")

        self.assertEqual(found[0], "NVDA")

    def test_a_company_name_matches(self):
        self.assertIn("AAPL", self._symbols("apple"))

    def test_a_crypto_base_resolves_to_the_dollar_pair_first(self):
        """BTC means BTC/USD, not BTC/USDT."""
        found = self._symbols("btc")

        self.assertEqual(found[0], "BTC/USD")

    def test_an_untradable_asset_ranks_below_a_tradable_one(self):
        found = self._symbols("halted")

        self.assertEqual(found, ["HALTED"])

    def test_an_empty_query_offers_a_default_shortlist(self):
        found = self._symbols("")

        self.assertIn("NVDA", found)
        self.assertIn("BTC/USD", found)

    def test_the_result_count_is_capped(self):
        self.assertEqual(len(self._symbols("", limit=3)), 3)
        self.assertLessEqual(len(self._symbols("a", limit=2)), 2)

    def test_a_query_matching_nothing_returns_nothing(self):
        self.assertEqual(self._symbols("zzzzzz"), [])

    def test_the_separator_is_ignored_in_the_query(self):
        self.assertIn("BTC/USD", self._symbols("btc/usd"))
        self.assertIn("BTC/USD", self._symbols("btc-usd"))


class CompanyNameTests(BrokerFixture):
    def test_the_broker_name_is_used(self):
        self._client(Client((_asset("NVDA", name="NVIDIA Corporation"),)))

        self.assertEqual(AlpacaUtils.get_company_name("NVDA"), "NVIDIA Corporation")

    def test_a_crypto_pair_is_its_own_name(self):
        self.assertEqual(AlpacaUtils.get_company_name("BTC/USD"), "BTC/USD")

    def test_a_nameless_asset_falls_back_to_the_known_company_list(self):
        self._client(Client((_asset("AAPL", name=""),)))

        self.assertTrue(AlpacaUtils.get_company_name("AAPL"))

    def test_an_unreachable_broker_falls_back_too(self):
        self._client(Client(error=RuntimeError("credentials rejected")))

        self.assertEqual(AlpacaUtils.get_company_name("ZZZZ"), "ZZZZ")


def _position(symbol="NVDA", **overrides):
    fields = {
        "symbol": symbol,
        "qty": "10",
        "current_price": "120.0",
        "avg_entry_price": "100.0",
        "market_value": "1200.0",
        "unrealized_intraday_pl": "25.0",
        "unrealized_pl": "200.0",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class PositionsTests(BrokerFixture):
    def test_a_position_is_rendered_for_the_panel(self):
        self._client(Client(positions=[_position()]))

        rows = AlpacaUtils.get_positions_data()

        self.assertEqual(rows[0]["Symbol"], "NVDA")
        self.assertEqual(rows[0]["Current Price"], "$120.00")
        self.assertEqual(rows[0]["Cost Basis"], "$1000.00")

    def test_profit_is_expressed_against_cost_basis(self):
        self._client(Client(positions=[_position()]))

        row = AlpacaUtils.get_positions_data()[0]

        self.assertEqual(row["Total P/L ($)"], "$200.00")
        self.assertEqual(row["Total P/L (%)"], "20.00%")
        self.assertEqual(row["Today's P/L (%)"], "2.50%")

    def test_a_zero_cost_basis_does_not_divide_by_zero(self):
        self._client(Client(positions=[_position(qty="0")]))

        row = AlpacaUtils.get_positions_data()[0]

        self.assertEqual(row["Total P/L (%)"], "0.00%")

    def test_virtual_stops_are_shown_when_registered(self):
        self._client(Client(positions=[_position()]))

        with mock.patch(
            "tradingagents.dataflows.virtual_stops_manager.VirtualStopsManager"
            ".get_active_virtual_stops",
            lambda symbol: [{"stop_loss_price": 95.0, "take_profit_price": 140.0}],
        ):
            row = AlpacaUtils.get_positions_data()[0]

        self.assertEqual(row["Virtual Stop Loss"], "$95.00")
        self.assertEqual(row["Virtual Take Profit"], "$140.00")

    def test_no_virtual_stops_shows_a_dash(self):
        self._client(Client(positions=[_position()]))

        with mock.patch(
            "tradingagents.dataflows.virtual_stops_manager.VirtualStopsManager"
            ".get_active_virtual_stops",
            lambda symbol: [],
        ):
            row = AlpacaUtils.get_positions_data()[0]

        self.assertEqual(row["Virtual Stop Loss"], "-")

    def test_a_failing_stops_lookup_does_not_lose_the_position(self):
        self._client(Client(positions=[_position()]))

        with mock.patch(
            "tradingagents.dataflows.virtual_stops_manager.VirtualStopsManager"
            ".get_active_virtual_stops",
            mock.Mock(side_effect=RuntimeError("store unavailable")),
        ):
            rows = AlpacaUtils.get_positions_data()

        self.assertEqual(rows[0]["Symbol"], "NVDA")

    def test_an_unreachable_broker_yields_no_positions(self):
        self._client(Client(error=RuntimeError("credentials rejected")))

        self.assertEqual(AlpacaUtils.get_positions_data(), [])


class VirtualStopReaderTests(unittest.TestCase):
    def test_the_stops_are_passed_through(self):
        with mock.patch(
            "tradingagents.dataflows.virtual_stops_manager.VirtualStopsManager"
            ".get_active_virtual_stops",
            lambda symbol: [{"symbol": symbol}],
        ):
            self.assertEqual(
                AlpacaUtils.get_active_virtual_stops("NVDA"), [{"symbol": "NVDA"}]
            )

    def test_a_failing_store_yields_nothing(self):
        with mock.patch(
            "tradingagents.dataflows.virtual_stops_manager.VirtualStopsManager"
            ".get_active_virtual_stops",
            mock.Mock(side_effect=RuntimeError("store unavailable")),
        ):
            self.assertEqual(AlpacaUtils.get_active_virtual_stops("NVDA"), [])


class TradeableUniverseTests(BrokerFixture):
    def test_a_listed_equity_is_tradeable(self):
        self._client(Client((_asset("NVDA"),)))

        self.assertEqual(AlpacaUtils.get_tradeable_assets(), {"NVDA": "stock"})

    def test_a_crypto_pair_is_normalized(self):
        self._client(
            Client((_asset("BTC/USD", asset_class=AssetClass.CRYPTO, exchange="CRYPTO"),))
        )

        self.assertEqual(AlpacaUtils.get_tradeable_assets(), {"BTC/USD": "crypto"})

    def test_an_untradable_asset_is_excluded(self):
        self._client(Client((_asset("HALTED", tradable=False),)))

        self.assertEqual(AlpacaUtils.get_tradeable_assets(), {})

    def test_derivatives_are_excluded(self):
        """Warrants, preferreds and rights are not what an analyst means."""
        self._client(
            Client((_asset("ABC.WS"), _asset("ABC.PR"), _asset("ABC.RT")))
        )

        self.assertEqual(AlpacaUtils.get_tradeable_assets(), {})

    def test_a_non_fractionable_otc_listing_is_excluded(self):
        self._client(
            Client((_asset("PENNY", exchange="OTC", fractionable=False),))
        )

        self.assertEqual(AlpacaUtils.get_tradeable_assets(), {})

    def test_a_fractionable_otc_adr_is_kept(self):
        self._client(Client((_asset("ADRCO", exchange="OTC", fractionable=True),)))

        self.assertEqual(AlpacaUtils.get_tradeable_assets(), {"ADRCO": "stock"})

    def test_a_thinly_listed_equity_is_excluded(self):
        self._client(
            Client((_asset("THIN", exchange="OTHER", fractionable=False),))
        )

        self.assertEqual(AlpacaUtils.get_tradeable_assets(), {})

    def test_a_major_exchange_listing_is_kept_even_if_whole_share_only(self):
        self._client(Client((_asset("BIG", exchange="NYSE", fractionable=False),)))

        self.assertEqual(AlpacaUtils.get_tradeable_assets(), {"BIG": "stock"})

    def test_an_unnamed_asset_is_skipped(self):
        self._client(Client((_asset(""),)))

        self.assertEqual(AlpacaUtils.get_tradeable_assets(), {})

    def test_the_universe_is_cached(self):
        client = self._client(Client((_asset("NVDA"),)))

        AlpacaUtils.get_tradeable_assets()
        AlpacaUtils.get_tradeable_assets()

        self.assertEqual(len(client.requests), 1)

    def test_an_unreachable_broker_yields_an_empty_universe(self):
        self._client(Client(error=RuntimeError("credentials rejected")))

        self.assertEqual(AlpacaUtils.get_tradeable_assets(), {})


class OpenOrderTests(BrokerFixture):
    def test_the_symbols_with_working_orders_come_back(self):
        self._client(
            Client(orders=[SimpleNamespace(symbol="nvda"), SimpleNamespace(symbol="AAPL")])
        )

        self.assertEqual(AlpacaUtils.get_open_orders(), {"NVDA", "AAPL"})

    def test_an_order_without_a_symbol_is_skipped(self):
        self._client(Client(orders=[SimpleNamespace(symbol="")]))

        self.assertEqual(AlpacaUtils.get_open_orders(), set())

    def test_an_unreachable_broker_reports_nothing_pending(self):
        self._client(Client(error=RuntimeError("credentials rejected")))

        self.assertEqual(AlpacaUtils.get_open_orders(), set())


if __name__ == "__main__":
    unittest.main()
