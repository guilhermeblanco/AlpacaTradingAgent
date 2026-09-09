"""Tests for the WebUI auto-trade path.

This is what turns a finished analysis into a broker order. Everything it
guards against — an unfinished run, no decision, a portfolio layer with no
headroom — must stop the order rather than send a wrong one, and the
sizing layers around it are explicitly failure-isolated.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from webui.components import analysis as an
from webui.utils.state import AppState


class TradeExecutionTests(unittest.TestCase):
    def setUp(self):
        self.state = AppState()
        patch = mock.patch("webui.components.analysis.app_state", self.state)
        patch.start()
        self.addCleanup(patch.stop)

        self.executed = []

        def record(ticker, intent, amount, **kwargs):
            self.executed.append(
                {"ticker": ticker, "intent": intent, "amount": amount, **kwargs}
            )
            return {"success": True}

        execute_patch = mock.patch.object(
            an, "execute_autonomous_trade", side_effect=record
        )
        execute_patch.start()
        self.addCleanup(execute_patch.stop)

        # The portfolio layer reaches for a broker snapshot and a data
        # provider before sizing; without these it fails closed to the
        # requested amount and the sizing assertions would be vacuous.
        broker = mock.patch(
            "tradingagents.broker.get_execution_broker_runtime",
            lambda *a, **k: SimpleNamespace(snapshot_provider=SimpleNamespace()),
        )
        broker.start()
        self.addCleanup(broker.stop)
        market = mock.patch(
            "tradingagents.marketdata.get_research_market_data_provider",
            lambda *a, **k: SimpleNamespace(),
        )
        market.start()
        self.addCleanup(market.stop)

    def _ready(self, *, symbol="NVDA", intent=None, action=None, decision=None):
        self.state.init_symbol_state(symbol)
        self.state.current_symbol = symbol
        state = self.state.get_state(symbol)
        state["analysis_complete"] = True
        if intent is not None:
            state["final_trade_intent"] = intent
        if action is not None:
            state["recommended_action"] = action
        if decision is not None:
            state["current_reports"]["final_trade_decision"] = decision
        return state

    def _run(self, ticker="NVDA", allow_shorts=False, amount=1000, sizing=1.0):
        with mock.patch(
            "tradingagents.portfolio.adjust_new_position_notional",
            lambda **kwargs: kwargs["requested_notional"] * sizing,
        ), mock.patch(
            "tradingagents.regime.regime_risk_multiplier", lambda *a, **k: 1.0
        ):
            an.execute_trade_after_analysis(ticker, allow_shorts, amount)

    def test_an_unknown_symbol_sends_nothing(self):
        self._run("UNKNOWN")

        self.assertEqual(self.executed, [])

    def test_an_unfinished_analysis_sends_nothing(self):
        """Half a run is not a decision."""
        self.state.init_symbol_state("NVDA")
        self.state.current_symbol = "NVDA"

        self._run()

        self.assertEqual(self.executed, [])

    def test_no_decision_at_all_sends_nothing(self):
        self._ready()

        self._run()

        self.assertEqual(self.executed, [])

    def test_a_typed_intent_is_executed(self):
        intent = {"action": "BUY", "symbol": "NVDA"}
        self._ready(intent=intent, action="BUY")

        self._run()

        self.assertEqual(len(self.executed), 1)
        self.assertEqual(self.executed[0]["intent"], intent)
        self.assertEqual(self.executed[0]["ticker"], "NVDA")

    def test_the_typed_intent_supplies_the_action_when_none_was_recorded(self):
        self._ready(intent={"action": "BUY", "symbol": "NVDA"})

        self._run()

        self.assertEqual(len(self.executed), 1)

    def test_the_action_is_recovered_from_the_written_decision(self):
        """Older runs recorded only the prose decision and no typed intent,
        so they take the legacy path that verifies the position first."""
        self._ready(decision="FINAL TRANSACTION PROPOSAL: BUY")
        legacy = mock.MagicMock(return_value={"actions": []})

        with mock.patch.object(
            an, "extract_recommendation", lambda *a, **k: "BUY"
        ), mock.patch.object(
            an.AlpacaUtils, "get_current_position_state", lambda *a, **k: "NEUTRAL"
        ), mock.patch.object(an.AlpacaUtils, "execute_trading_action", legacy):
            self._run()

        self.assertEqual(self.executed, [])
        legacy.assert_called_once()
        self.assertEqual(legacy.call_args.kwargs["signal"], "BUY")

    def test_an_unverifiable_position_stops_the_legacy_path(self):
        """Guessing NEUTRAL could open a position instead of closing one."""
        state = self._ready(decision="FINAL TRANSACTION PROPOSAL: BUY")
        legacy = mock.MagicMock(return_value={"actions": []})

        def explode(*_a, **_k):
            raise RuntimeError("broker unreachable")

        with mock.patch.object(
            an, "extract_recommendation", lambda *a, **k: "BUY"
        ), mock.patch.object(
            an.AlpacaUtils, "get_current_position_state", explode
        ), mock.patch.object(an.AlpacaUtils, "execute_trading_action", legacy):
            self._run()

        legacy.assert_not_called()
        self.assertIn("error", state["trading_results"])

    def test_an_intent_stored_under_the_results_is_found(self):
        state = self._ready(action="BUY")
        state["analysis_results"] = {"trade_intent": {"action": "BUY"}}

        self._run()

        self.assertEqual(self.executed[0]["intent"], {"action": "BUY"})

    def test_an_intent_nested_in_the_full_state_is_found(self):
        state = self._ready(action="BUY")
        state["analysis_results"] = {
            "full_state": {"final_trade_intent": {"action": "BUY"}}
        }

        self._run()

        self.assertEqual(self.executed[0]["intent"], {"action": "BUY"})

    def test_the_portfolio_layer_can_reduce_the_order(self):
        self._ready(intent={"action": "BUY"}, action="BUY")

        self._run(amount=1000, sizing=0.5)

        self.assertEqual(self.executed[0]["amount"], 500)

    def test_no_gross_exposure_headroom_cancels_the_order(self):
        """Zero size is a refusal, not a zero-dollar order."""
        self._ready(intent={"action": "BUY"}, action="BUY")

        self._run(amount=1000, sizing=0.0)

        self.assertEqual(self.executed, [])

    def test_a_failing_portfolio_layer_keeps_the_requested_amount(self):
        self._ready(intent={"action": "BUY"}, action="BUY")

        def explode(**_kwargs):
            raise RuntimeError("no broker snapshot")

        with mock.patch(
            "tradingagents.portfolio.adjust_new_position_notional", explode
        ), mock.patch("tradingagents.regime.regime_risk_multiplier", lambda *a, **k: 1.0):
            an.execute_trade_after_analysis("NVDA", False, 1000)

        self.assertEqual(self.executed[0]["amount"], 1000)

    def test_a_hostile_regime_shrinks_new_exposure(self):
        self._ready(intent={"action": "BUY"}, action="BUY")

        with mock.patch(
            "tradingagents.portfolio.adjust_new_position_notional",
            lambda **kwargs: kwargs["requested_notional"],
        ), mock.patch(
            "tradingagents.regime.regime_risk_multiplier", lambda *a, **k: 0.5
        ):
            an.execute_trade_after_analysis("NVDA", False, 1000)

        self.assertEqual(self.executed[0]["amount"], 500)

    def test_the_regime_filter_only_shrinks_opening_trades(self):
        """Selling out of a hostile regime must not be scaled down."""
        self._ready(intent={"action": "SELL"}, action="SELL")

        with mock.patch(
            "tradingagents.portfolio.adjust_new_position_notional",
            lambda **kwargs: kwargs["requested_notional"],
        ), mock.patch(
            "tradingagents.regime.regime_risk_multiplier", lambda *a, **k: 0.5
        ):
            an.execute_trade_after_analysis("NVDA", False, 1000)

        self.assertEqual(self.executed[0]["amount"], 1000)

    def test_a_failing_regime_lookup_keeps_the_requested_amount(self):
        self._ready(intent={"action": "BUY"}, action="BUY")

        def explode(*_a, **_k):
            raise RuntimeError("no price history")

        with mock.patch(
            "tradingagents.portfolio.adjust_new_position_notional",
            lambda **kwargs: kwargs["requested_notional"],
        ), mock.patch("tradingagents.regime.regime_risk_multiplier", explode):
            an.execute_trade_after_analysis("NVDA", False, 1000)

        self.assertEqual(self.executed[0]["amount"], 1000)

    def test_a_broker_failure_does_not_escape_the_analysis_thread(self):
        """This runs on the background thread; an escape would kill the run."""
        self._ready(intent={"action": "BUY"}, action="BUY")

        with mock.patch.object(
            an, "execute_autonomous_trade", side_effect=RuntimeError("broker down")
        ):
            try:
                self._run()
            except RuntimeError:
                self.fail("a broker failure must not propagate")


if __name__ == "__main__":
    unittest.main()
