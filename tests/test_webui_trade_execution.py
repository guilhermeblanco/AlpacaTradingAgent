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


class StartAnalysisTests(unittest.TestCase):
    """The entry point the Start button calls, before any model runs."""

    def setUp(self):
        self.state = AppState()
        patch = mock.patch("webui.components.analysis.app_state", self.state)
        patch.start()
        self.addCleanup(patch.stop)
        self.state.init_symbol_state("NVDA")
        self.state.current_symbol = "NVDA"

        self.runs = []
        run_patch = mock.patch.object(
            an, "run_analysis", side_effect=lambda *a, **k: self.runs.append((a, k))
        )
        run_patch.start()
        self.addCleanup(run_patch.stop)

        chart = mock.patch.object(an, "create_chart", lambda *a, **k: "chart")
        chart.start()
        self.addCleanup(chart.stop)

    def _start(self, **overrides):
        params = {
            "ticker": "NVDA",
            "analysts_market": True,
            "analysts_social": False,
            "analysts_news": False,
            "analysts_fundamentals": False,
            "analysts_macro": False,
            "research_depth": "Medium",
            "allow_shorts": False,
            "quick_llm": "gpt-5.4-nano",
            "deep_llm": "gpt-5.4-mini",
        }
        params.update(overrides)
        return an.start_analysis(**params)

    def _allow_budget(self):
        guard = mock.MagicMock()
        guard.check_llm_budget.return_value = SimpleNamespace(allowed=True, reasons=[])
        return mock.patch("tradingagents.safety.get_safety_guard", lambda: guard)

    def test_an_exhausted_token_budget_refuses_before_spending_more(self):
        """The gate exists to stop a runaway from burning the day's budget."""
        guard = mock.MagicMock()
        guard.check_llm_budget.return_value = SimpleNamespace(
            allowed=False, reasons=["daily LLM token budget exhausted"]
        )

        with mock.patch("tradingagents.safety.get_safety_guard", lambda: guard):
            message = self._start()

        self.assertIn("budget exhausted", message)
        self.assertEqual(self.runs, [])

    def test_an_unavailable_guard_does_not_block_the_run(self):
        with mock.patch(
            "tradingagents.safety.get_safety_guard", side_effect=RuntimeError("no db")
        ):
            self._start()

        self.assertEqual(len(self.runs), 1)

    def test_no_analysts_is_refused(self):
        with self._allow_budget():
            message = self._start(analysts_market=False)

        self.assertIn("at least one analyst", message)
        self.assertEqual(self.runs, [])

    def test_the_selected_analysts_are_passed_through_in_order(self):
        with self._allow_budget():
            self._start(
                analysts_market=True,
                analysts_social=True,
                analysts_news=False,
                analysts_fundamentals=True,
                analysts_macro=True,
            )

        args, _kwargs = self.runs[0]
        self.assertEqual(args[1], ["market", "social", "fundamentals", "macro"])

    def test_each_research_depth_maps_to_its_round_count(self):
        for depth, rounds in (("Shallow", 1), ("Medium", 3), ("Deep", 5)):
            self.runs.clear()
            with self._allow_budget():
                self._start(research_depth=depth)

            args, _kwargs = self.runs[0]
            self.assertEqual(args[2], {"rounds": rounds, "level": depth}, depth)

    def test_the_initial_chart_is_drawn_before_the_run(self):
        with self._allow_budget():
            self._start()

        self.assertEqual(self.state.get_state("NVDA")["chart_data"], "chart")

    def test_a_failing_initial_chart_does_not_stop_the_run(self):
        def explode(*_a, **_k):
            raise RuntimeError("provider down")

        with self._allow_budget(), mock.patch.object(an, "create_chart", explode):
            self._start()

        self.assertEqual(len(self.runs), 1)

    def test_the_status_message_names_the_trading_mode(self):
        with self._allow_budget():
            investment = self._start(allow_shorts=False)
            trading = self._start(allow_shorts=True)

        self.assertIn("Investment Mode", investment)
        self.assertIn("Trading Mode", trading)

    def test_the_status_message_mentions_order_execution_when_enabled(self):
        self.state.trade_enabled = True
        self.state.trade_amount = 2500

        with self._allow_budget():
            message = self._start()

        self.assertIn("2500", message)

    def test_the_status_message_omits_execution_when_disabled(self):
        self.state.trade_enabled = False

        with self._allow_budget():
            message = self._start()

        self.assertNotIn("order execution", message)


class RunAnalysisGuardTests(unittest.TestCase):
    def setUp(self):
        self.state = AppState()
        patch = mock.patch("webui.components.analysis.app_state", self.state)
        patch.start()
        self.addCleanup(patch.stop)

    def test_an_unknown_symbol_is_refused_before_building_a_graph(self):
        built = []

        with mock.patch.object(
            an, "TradingAgentsGraph", lambda *a, **k: built.append(1)
        ), mock.patch.object(an, "get_run_audit_logger", mock.MagicMock()):
            an.run_analysis(
                "UNKNOWN", ["market"], {"rounds": 1, "level": "Shallow"}, False,
                "gpt-5.4-nano", "gpt-5.4-mini",
            )

        self.assertEqual(built, [])


class RunAnalysisConfigTests(unittest.TestCase):
    """run_analysis turns UI selections into the graph's configuration."""

    def setUp(self):
        self.state = AppState()
        patch = mock.patch("webui.components.analysis.app_state", self.state)
        patch.start()
        self.addCleanup(patch.stop)
        self.state.init_symbol_state("NVDA")
        self.state.current_symbol = "NVDA"
        self.state.analyzing_symbol = "NVDA"

        self.built = {}
        graph = mock.MagicMock()
        graph.propagate.side_effect = RuntimeError("stop after configuration")

        def build(analysts, config=None, **kwargs):
            self.built["analysts"] = analysts
            self.built["config"] = config
            return graph

        for target, replacement in (
            ("TradingAgentsGraph", build),
            ("get_run_audit_logger", mock.MagicMock()),
        ):
            layer = mock.patch.object(an, target, replacement)
            layer.start()
            self.addCleanup(layer.stop)

    def _run(self, **overrides):
        params = {
            "ticker": "NVDA",
            "selected_analysts": ["market"],
            "research_depth_config": {"rounds": 3, "level": "Medium"},
            "allow_shorts": False,
            "quick_llm": "gpt-5.4-nano",
            "deep_llm": "gpt-5.4-mini",
        }
        params.update(overrides)
        an.run_analysis(**params)
        return self.built.get("config") or {}

    def test_the_depth_config_sets_both_debate_budgets(self):
        config = self._run(research_depth_config={"rounds": 5, "level": "Deep"})

        self.assertEqual(config["max_debate_rounds"], 5)
        self.assertEqual(config["max_risk_discuss_rounds"], 5)
        self.assertEqual(config["research_depth"], "Deep")

    def test_an_integer_depth_is_still_accepted(self):
        """Older callers passed the round count directly."""
        config = self._run(research_depth_config=3)

        self.assertEqual(config["max_debate_rounds"], 3)

    def test_shorting_selects_trading_mode(self):
        self.assertEqual(self._run(allow_shorts=True)["trading_mode"], "trading")
        self.assertEqual(self._run(allow_shorts=False)["trading_mode"], "investment")

    def test_the_models_and_provider_are_carried_over(self):
        config = self._run(
            quick_llm="claude-haiku-4-5-20251001",
            deep_llm="claude-opus-5",
            llm_provider="anthropic",
        )

        self.assertEqual(config["quick_think_llm"], "claude-haiku-4-5-20251001")
        self.assertEqual(config["deep_think_llm"], "claude-opus-5")
        self.assertEqual(config["llm_provider"], "anthropic")

    def test_a_blank_endpoint_becomes_none(self):
        self.assertIsNone(self._run(backend_url="")["backend_url"])

    def test_the_output_language_defaults_to_english(self):
        self.assertEqual(self._run(output_language="")["output_language"], "English")

    def test_the_selected_analysts_reach_the_graph(self):
        self._run(selected_analysts=["market", "macro"])

        self.assertEqual(self.built["analysts"], ["market", "macro"])

    def test_a_graph_failure_clears_the_running_flag(self):
        self._run()

        self.assertFalse(self.state.get_state("NVDA")["analysis_running"])
