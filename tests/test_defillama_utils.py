"""Tests for the DeFi Llama fundamentals summary.

This is the fundamentals analyst's only source for a token that has no
income statement, and it answers in markdown that goes straight into a
prompt. Every failure path therefore has to produce prose — a raised
exception here takes the analyst node down with it.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

import requests

from tradingagents.dataflows import defillama_utils
from tradingagents.dataflows.defillama_utils import _find_slug, get_fundamentals

DAY = 86_400
NOW = 1_767_225_600  # 2026-01-01T00:00:00Z


def _tvl_series(points):
    return [{"date": ts, "totalLiquidityUSD": tvl} for ts, tvl in points]


class Endpoints:
    """Stands in for the whole API surface, keyed by endpoint path."""

    def __init__(self, responses, *, errors=None):
        self.responses = responses
        self.errors = errors or {}
        self.requested = []

    def __call__(self, endpoint):
        self.requested.append(endpoint)
        if endpoint in self.errors:
            raise self.errors[endpoint]
        if endpoint not in self.responses:
            raise requests.exceptions.HTTPError(f"404 for {endpoint}")
        return self.responses[endpoint]

    def install(self, test):
        patcher = mock.patch.object(defillama_utils, "_fetch_json", self)
        patcher.start()
        test.addCleanup(patcher.stop)
        return self


class FetchTests(unittest.TestCase):
    def test_the_request_is_bounded_by_a_timeout(self):
        captured = {}

        def get(url, timeout=None):
            captured["url"] = url
            captured["timeout"] = timeout
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {})

        with mock.patch.object(defillama_utils.requests, "get", get):
            defillama_utils._fetch_json("/protocols")

        self.assertEqual(captured["url"], f"{defillama_utils.BASE_URL}/protocols")
        self.assertEqual(captured["timeout"], 10)


class SlugLookupTests(unittest.TestCase):
    PROTOCOLS = [
        {"symbol": "UNI", "slug": "uniswap", "name": "Uniswap"},
        {"symbol": ["GMX", "GLP"], "slug": "gmx", "name": "GMX"},
        {"slug": "no-symbol", "name": "Unnamed"},
        {"symbol": None, "slug": "null-symbol"},
    ]

    def _lookup(self, symbol):
        with mock.patch.object(
            defillama_utils, "_get_protocols", lambda: self.PROTOCOLS
        ):
            return _find_slug(symbol)

    def test_a_symbol_resolves_to_its_slug_and_name(self):
        self.assertEqual(self._lookup("UNI"), ("uniswap", "Uniswap"))

    def test_the_lookup_is_case_insensitive(self):
        self.assertEqual(self._lookup("uni"), ("uniswap", "Uniswap"))

    def test_a_protocol_listing_several_symbols_matches_any_of_them(self):
        self.assertEqual(self._lookup("GLP"), ("gmx", "GMX"))

    def test_protocols_without_a_symbol_are_skipped(self):
        self.assertEqual(self._lookup("MISSING"), (None, None))


class ProtocolFundamentalsTests(unittest.TestCase):
    PROTOCOLS = [{"symbol": "UNI", "slug": "uniswap", "name": "Uniswap"}]

    def _endpoints(self, **overrides):
        responses = {
            "/protocols": self.PROTOCOLS,
            "/protocol/uniswap": {
                "tvl": _tvl_series(
                    [(NOW - 60 * DAY, 1_000_000.0), (NOW - 30 * DAY, 2_000_000.0), (NOW, 3_000_000.0)]
                )
            },
            "/summary/fees/uniswap": {
                "totalDataChart": [[NOW - 40 * DAY, 100.0], [NOW - 10 * DAY, 400.0], [NOW, 500.0]],
                "revenueDataChart": [[NOW - 10 * DAY, 40.0], [NOW, 60.0]],
            },
        }
        responses.update(overrides.pop("responses", {}))
        return Endpoints(responses, **overrides).install(self)

    def test_the_summary_names_the_protocol_and_the_as_of_date(self):
        self._endpoints()

        summary = get_fundamentals("UNI")

        self.assertIn("### Uniswap Fundamentals", summary)
        self.assertIn("2026-01-01", summary)

    def test_the_latest_tvl_is_reported(self):
        self._endpoints()

        self.assertIn("**Latest TVL:** $3,000,000", get_fundamentals("UNI"))

    def test_the_tvl_change_is_measured_from_the_lookback_cutoff(self):
        """2m 30 days ago against 3m now is +50%."""
        self._endpoints()

        self.assertIn("**TVL Δ 30d:** +50.00%", get_fundamentals("UNI", 30))

    def test_a_series_with_no_point_before_the_cutoff_omits_the_change(self):
        self._endpoints(
            responses={"/protocol/uniswap": {"tvl": _tvl_series([(NOW, 3_000_000.0)])}}
        )

        self.assertNotIn("TVL Δ", get_fundamentals("UNI", lookback_days=30))

    def test_a_zero_baseline_omits_the_change_rather_than_dividing_by_it(self):
        self._endpoints(
            responses={
                "/protocol/uniswap": {
                    "tvl": _tvl_series([(NOW - 60 * DAY, 0.0), (NOW, 3_000_000.0)])
                }
            }
        )

        self.assertNotIn("TVL Δ", get_fundamentals("UNI", lookback_days=30))

    def test_fees_and_revenue_are_summed_over_the_window(self):
        self._endpoints()

        summary = get_fundamentals("UNI", 30)

        self.assertIn("**Fees collected (30d):** $900", summary)
        self.assertIn("**Revenue (30d):** $100", summary)

    def test_a_protocol_without_fee_data_still_reports_tvl(self):
        """Most protocols have no fee series; that is not an error."""
        self._endpoints(errors={"/summary/fees/uniswap": RuntimeError("no fees")})

        summary = get_fundamentals("UNI")

        self.assertIn("Latest TVL", summary)
        self.assertNotIn("Fees collected", summary)

    def test_an_empty_fee_chart_is_omitted_rather_than_reported_as_zero(self):
        self._endpoints(
            responses={
                "/summary/fees/uniswap": {"totalDataChart": [], "revenueDataChart": []}
            }
        )

        self.assertNotIn("Fees collected", get_fundamentals("UNI"))

    def test_a_protocol_with_no_tvl_history_says_so(self):
        self._endpoints(responses={"/protocol/uniswap": {"tvl": []}})

        self.assertIn("No TVL data available", get_fundamentals("UNI"))

    def test_a_failing_tvl_request_is_reported_as_markdown(self):
        self._endpoints(errors={"/protocol/uniswap": RuntimeError("503")})

        self.assertIn("Error fetching TVL data", get_fundamentals("UNI"))

    def test_a_failing_protocol_list_is_reported_as_markdown(self):
        """The lookup was the one path in this module that raised."""
        with mock.patch.object(
            defillama_utils,
            "_get_protocols",
            mock.Mock(side_effect=RuntimeError("gateway timeout")),
        ):
            summary = get_fundamentals("UNI")

        self.assertIn("Error fetching DeFiLlama protocol list", summary)
        self.assertIn("gateway timeout", summary)


class ChainFundamentalsTests(unittest.TestCase):
    def _endpoints(self, **overrides):
        responses = {
            "/v2/historicalChainTvl/Ethereum": [
                {"date": NOW - 30 * DAY, "tvl": 50_000_000.0},
                {"date": NOW, "tvl": 60_000_000.0},
            ],
            "/protocols": [
                {"chains": ["Ethereum"]},
                {"chains": ["ethereum", "Arbitrum"]},
                {"chains": ["Solana"]},
            ],
        }
        responses.update(overrides.pop("responses", {}))
        return Endpoints(responses, **overrides).install(self)

    def test_a_base_chain_uses_chain_level_data_without_a_slug_lookup(self):
        endpoints = self._endpoints()

        summary = get_fundamentals("ETH")

        self.assertIn("### Ethereum Ecosystem Fundamentals", summary)
        self.assertIn("/v2/historicalChainTvl/Ethereum", endpoints.requested)

    def test_the_chain_tvl_and_its_change_are_reported(self):
        self._endpoints()

        summary = get_fundamentals("ETH", 30)

        self.assertIn("**Total TVL:** $60,000,000", summary)
        self.assertIn("**TVL Δ 30d:** +20.00%", summary)

    def test_the_protocol_count_is_matched_case_insensitively(self):
        self._endpoints()

        self.assertIn("**Active Protocols:** 2", get_fundamentals("ETH"))

    def test_a_failing_protocol_count_does_not_lose_the_tvl(self):
        self._endpoints(errors={"/protocols": RuntimeError("503")})

        summary = get_fundamentals("ETH")

        self.assertIn("Total TVL", summary)
        self.assertNotIn("Active Protocols", summary)

    def test_an_unmapped_symbol_lists_what_is_supported(self):
        Endpoints({"/protocols": []}).install(self)

        summary = get_fundamentals("NOTACHAIN")

        self.assertIn("not recognized", summary)
        self.assertIn("ETH", summary)

    def test_a_token_with_no_protocol_entry_falls_back_to_chain_lookup(self):
        Endpoints({"/protocols": []}).install(self)

        self.assertIn("not recognized", get_fundamentals("SOMETOKEN"))

    def test_an_empty_chain_history_says_so(self):
        self._endpoints(responses={"/v2/historicalChainTvl/Ethereum": []})

        self.assertIn("No TVL data available", get_fundamentals("ETH"))

    def test_a_failing_chain_request_is_reported_as_markdown(self):
        self._endpoints(errors={"/v2/historicalChainTvl/Ethereum": RuntimeError("503")})

        self.assertIn("Error fetching chain fundamentals", get_fundamentals("ETH"))


if __name__ == "__main__":
    unittest.main()
