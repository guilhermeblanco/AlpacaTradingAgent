"""Tests for the broker snapshot the trader and risk prompts read.

This is the only place where live account state enters an LLM prompt. If
the broker is unreachable it has to say so in words the model can act on —
a silent zero would read as "flat with no equity" and invite a full-size
entry.
"""

from __future__ import annotations

import unittest

from tradingagents.broker.models import (
    AccountSnapshot,
    PortfolioSnapshot,
    PositionSnapshot,
)
from tradingagents.broker.prompt_context import build_broker_prompt_context


class Provider:
    def __init__(self, snapshot=None, error=None):
        self._snapshot = snapshot
        self._error = error

    def get_portfolio_snapshot(self):
        if self._error:
            raise self._error
        return self._snapshot


def _position(**overrides):
    fields = {
        "symbol": "NVDA",
        "quantity": 10.0,
        "side": "LONG",
        "average_entry_price": 100.0,
        "market_value": 1_200.0,
        "unrealized_pl": 200.0,
        "unrealized_intraday_pl": 25.0,
    }
    fields.update(overrides)
    return PositionSnapshot(**fields)


def _snapshot(*, positions=(), **account):
    fields = {
        "equity": 100_000.0,
        "buying_power": 50_000.0,
        "cash": 25_000.0,
        "last_equity": 99_000.0,
    }
    fields.update(account)
    return PortfolioSnapshot(
        account=AccountSnapshot(**fields), positions=list(positions)
    )


class UnreachableBrokerTests(unittest.TestCase):
    def test_the_fallback_position_is_used(self):
        position, _stats, _account = build_broker_prompt_context(
            Provider(error=RuntimeError("timeout")), "NVDA", fallback_position="LONG"
        )

        self.assertEqual(position, "LONG")

    def test_the_prompt_says_the_data_is_unavailable_rather_than_zero(self):
        _side, stats, account = build_broker_prompt_context(
            Provider(error=RuntimeError("connection refused")), "NVDA"
        )

        self.assertIn("unavailable", stats)
        self.assertIn("connection refused", stats)
        self.assertIn("unavailable", account)


class OpenPositionTests(unittest.TestCase):
    def test_the_side_is_reported(self):
        position, _stats, _account = build_broker_prompt_context(
            Provider(_snapshot(positions=[_position()])), "NVDA"
        )

        self.assertEqual(position, "LONG")

    def test_the_position_figures_reach_the_prompt(self):
        _side, stats, _account = build_broker_prompt_context(
            Provider(_snapshot(positions=[_position()])), "NVDA"
        )

        self.assertIn("Quantity: 10", stats)
        self.assertIn("$100.00", stats)
        self.assertIn("$1,200.00", stats)
        self.assertIn("$25.00", stats)
        self.assertIn("$200.00", stats)

    def test_the_allocation_is_expressed_against_equity(self):
        _side, stats, _account = build_broker_prompt_context(
            Provider(_snapshot(positions=[_position(market_value=10_000.0)])), "NVDA"
        )

        self.assertIn("10.00%", stats)

    def test_a_zero_equity_account_does_not_divide_by_zero(self):
        _side, stats, _account = build_broker_prompt_context(
            Provider(_snapshot(positions=[_position()], equity=0.0)), "NVDA"
        )

        self.assertIn("0.00%", stats)

    def test_missing_cost_basis_reads_as_zero_rather_than_crashing(self):
        _side, stats, _account = build_broker_prompt_context(
            Provider(
                _snapshot(
                    positions=[
                        _position(
                            average_entry_price=None,
                            unrealized_pl=None,
                            unrealized_intraday_pl=None,
                        )
                    ]
                )
            ),
            "NVDA",
        )

        self.assertIn("Average Entry Price: $0.00", stats)

    def test_another_symbols_position_does_not_count(self):
        position, stats, _account = build_broker_prompt_context(
            Provider(_snapshot(positions=[_position(symbol="AAPL")])), "NVDA"
        )

        self.assertEqual(position, "NEUTRAL")
        self.assertIn("No open position", stats)


class FlatAccountTests(unittest.TestCase):
    def test_no_position_reads_as_neutral(self):
        position, stats, _account = build_broker_prompt_context(
            Provider(_snapshot()), "NVDA"
        )

        self.assertEqual(position, "NEUTRAL")
        self.assertIn("No open position", stats)

    def test_a_stale_fallback_does_not_override_a_live_flat_snapshot(self):
        """The snapshot is the truth once it arrives."""
        position, _stats, _account = build_broker_prompt_context(
            Provider(_snapshot()), "NVDA", fallback_position="LONG"
        )

        self.assertEqual(position, "NEUTRAL")


class AccountStatusTests(unittest.TestCase):
    def test_the_headline_balances_are_reported(self):
        _side, _stats, account = build_broker_prompt_context(
            Provider(_snapshot()), "NVDA"
        )

        self.assertIn("Equity: $100,000.00", account)
        self.assertIn("Buying Power: $50,000.00", account)
        self.assertIn("Cash: $25,000.00", account)

    def test_the_daily_change_is_computed_against_the_prior_close(self):
        _side, _stats, account = build_broker_prompt_context(
            Provider(_snapshot()), "NVDA"
        )

        self.assertIn("Daily Change: $1,000.00 (1.01%)", account)

    def test_a_missing_prior_close_reads_as_no_change(self):
        _side, _stats, account = build_broker_prompt_context(
            Provider(_snapshot(last_equity=None)), "NVDA"
        )

        self.assertIn("Daily Change: $0.00 (0.00%)", account)

    def test_gross_exposure_is_reported(self):
        _side, _stats, account = build_broker_prompt_context(
            Provider(_snapshot(positions=[_position(market_value=1_200.0)])), "NVDA"
        )

        self.assertIn("Gross Exposure: $1,200.00", account)


if __name__ == "__main__":
    unittest.main()
