"""Tests for the Decision Tape panel and its charts.

The tape reads only from PostgreSQL, because it has to show decisions from
autonomous cycles and past sessions rather than whatever this process
happens to be holding. Without a database it must say so, not render an
empty tape that reads like "nothing happened".
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import dash

from conftest import dash_callback
from tradingagents.execution.gates import GateLedger
from tradingagents.workbench.tape import build_tape
from webui.callbacks import workbench_callbacks
from webui.components.workbench import (
    empty_figure,
    evidence_figure,
    gate_waterfall_figure,
    outcome_figure,
    stage_rail,
)


def _summary(**overrides):
    fields = {
        "decision_id": "decision-1",
        "symbol": "NVDA",
        "status": "succeeded",
        "created_at": datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 9, 9, 14, 5, tzinfo=timezone.utc),
        "broker": "alpaca",
        "order_count": 1,
        "filled_quantity": 10.0,
        "error": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _analysis():
    return {
        "run_id": "run-1",
        "trade_date": "2026-09-09",
        "final_signal": "BUY",
        "gather": {
            "symbol": "NVDA",
            "trade_date": "2026-09-09",
            "current_position": "NEUTRAL",
            "provenance": {"score": 9.1},
        },
        "analyze": {
            "produced": 1,
            "expected": 5,
            "reports": [
                {
                    "report_key": "market_report",
                    "label": "Market",
                    "produced": True,
                    "chars": 400,
                }
            ],
        },
        "compute": {
            "available": True,
            "scoreboard": {
                "net_direction": "bullish",
                "net_confidence": "medium",
                "bullish_score": 0.6,
                "bearish_score": 0.1,
            },
            "sources": [
                {
                    "label": "Market",
                    "claims": [
                        {
                            "claim_id": "market_001",
                            "claim": "Momentum is positive.",
                            "direction": "bullish",
                            "confidence": 0.47,
                            "freshness": 0.62,
                            "numeric_support": 0.0,
                            "contradiction": 0.0,
                        }
                    ],
                }
            ],
            "totals": {"claims": 1},
        },
        "decide": {
            "research_debate": {
                "rounds": 2,
                "bull": {"text": "growth is accelerating"},
                "bear": {"text": "margins are peaking"},
                "transcript": "the debate",
                "verdict": "Accumulate.",
            },
            "risk_debate": {
                "rounds": 3,
                "risky": {"text": "size up"},
                "safe": {"text": "size down"},
                "transcript": "the risk debate",
                "verdict": "Half size.",
            },
            "final_decision": "FINAL TRANSACTION PROPOSAL: **BUY**",
            "recommended_action": "BUY",
        },
    }


def _tape(*, ledger=None, orders=None, outcomes=None, analysis=None, summary=None):
    return build_tape(
        summary=summary or _summary(),
        analysis=analysis if analysis is not None else _analysis(),
        gate_ledger=ledger,
        orders=orders or [],
        outcomes=outcomes or [],
    )


def _cleared_ledger():
    ledger = GateLedger(
        decision_id="decision-1", symbol="NVDA", requested_notional=1_000.0
    )
    ledger.passed("intent")
    ledger.clipped("risk_sizing", notional_before=1_000.0, notional_after=600.0)
    ledger.passed("safety", metrics={"daily_loss_pct": -1.2})
    return ledger


class ChartTests(unittest.TestCase):
    def test_the_waterfall_charts_the_start_the_clip_and_the_end(self):
        figure = gate_waterfall_figure(_cleared_ledger().waterfall())

        trace = figure.data[0]
        self.assertEqual(list(trace.x), ["Requested", "Risk sizing", "Sent"])
        self.assertEqual(list(trace.measure), ["absolute", "relative", "total"])

    def test_a_decision_with_no_ledger_says_so_rather_than_charting_zero(self):
        figure = gate_waterfall_figure([])

        self.assertIn("No gate ledger", figure.layout.annotations[0].text)

    def test_the_evidence_chart_shows_each_scoreboard_dimension(self):
        figure = evidence_figure(_tape().evidence_bars())

        self.assertIn("Bullish", list(figure.data[0].y))
        self.assertIn("Contradiction", list(figure.data[0].y))

    def test_the_evidence_chart_is_bounded_to_the_score_range(self):
        figure = evidence_figure(_tape().evidence_bars())

        self.assertEqual(tuple(figure.layout.xaxis.range), (0, 1))

    def test_no_evidence_says_so(self):
        self.assertIn("No scored evidence", evidence_figure([]).layout.annotations[0].text)

    def test_the_outcome_chart_colours_gains_and_losses_apart(self):
        figure = outcome_figure(
            [
                {"horizon": "1d", "excess_return_pct": 1.5, "directionally_correct": True},
                {"horizon": "5d", "excess_return_pct": -0.8, "directionally_correct": False},
            ]
        )

        colors = figure.data[0].marker.color
        self.assertEqual(len(set(colors)), 2)

    def test_an_unresolved_decision_says_so(self):
        self.assertIn("No horizon", outcome_figure([]).layout.annotations[0].text)

    def test_an_empty_figure_draws_no_axes(self):
        figure = empty_figure("nothing here")

        self.assertFalse(figure.layout.xaxis.visible)


class StageRailTests(unittest.TestCase):
    def test_every_stage_is_labelled_on_the_rail(self):
        rendered = str(stage_rail(_tape().stages))

        for label in ("Gather", "Analyze", "Compute", "Decide", "Prepare", "Order", "React"):
            self.assertIn(label, rendered)

    def test_the_active_stage_is_highlighted(self):
        rendered = str(stage_rail(_tape().stages, active="decide"))

        self.assertIn("boxShadow", rendered)

    def test_an_inactive_rail_has_no_highlight(self):
        rendered = str(stage_rail(_tape().stages, active=""))

        self.assertNotIn("0 0 0 3px", rendered)


class TapeRenderTests(unittest.TestCase):
    def _render(self, tape):
        return str(workbench_callbacks.render_tape(tape))

    def test_the_header_names_the_symbol_and_the_decision(self):
        rendered = self._render(_tape())

        self.assertIn("NVDA", rendered)
        self.assertIn("decision-1", rendered)

    def test_every_stage_gets_a_card(self):
        rendered = self._render(_tape())

        for label in ("Gather", "Analyze", "Compute", "Decide", "Prepare", "Order", "React"):
            self.assertIn(label, rendered)

    def test_the_scored_claims_are_rendered_as_a_table(self):
        rendered = self._render(_tape())

        self.assertIn("market_001", rendered)
        self.assertIn("Momentum is positive.", rendered)

    def test_both_debates_are_rendered_with_their_verdicts(self):
        rendered = self._render(_tape())

        self.assertIn("Research debate", rendered)
        self.assertIn("Risk debate", rendered)
        self.assertIn("Accumulate.", rendered)
        self.assertIn("Half size.", rendered)

    def test_the_gate_ledger_is_rendered_gate_by_gate(self):
        rendered = self._render(_tape(ledger=_cleared_ledger()))

        self.assertIn("Risk sizing", rendered)
        self.assertIn("CLIPPED", rendered)
        self.assertIn("$1,000 → $600", rendered)

    def test_a_gate_metric_is_shown_alongside_its_verdict(self):
        rendered = self._render(_tape(ledger=_cleared_ledger()))

        self.assertIn("daily_loss_pct", rendered)

    def test_a_halted_decision_leads_with_what_stopped_it(self):
        ledger = GateLedger(
            decision_id="decision-1", symbol="NVDA", requested_notional=1_000.0
        )
        ledger.blocked("safety", reasons=["daily loss breaker tripped"])

        rendered = self._render(_tape(ledger=ledger, summary=_summary(status="blocked")))

        self.assertIn("Halted at Prepare", rendered)
        self.assertIn("daily loss breaker tripped", rendered)

    def test_orders_are_rendered_with_their_fills(self):
        rendered = self._render(
            _tape(
                orders=[
                    {
                        "broker_order_id": "order-1",
                        "side": "buy",
                        "status": "filled",
                        "filled_quantity": 10.0,
                        "filled_avg_price": 100.0,
                    }
                ]
            )
        )

        self.assertIn("order-1", rendered)

    def test_outcomes_are_rendered_per_horizon(self):
        rendered = self._render(
            _tape(
                outcomes=[
                    {
                        "horizon": "1d",
                        "excess_return_pct": 1.5,
                        "directionally_correct": True,
                    }
                ]
            )
        )

        self.assertIn("1d: +1.50%", rendered)

    def test_a_decision_with_no_recorded_analysis_still_renders(self):
        rendered = self._render(_tape(analysis={}))

        self.assertIn("Gather", rendered)
        self.assertIn("No scored claims", rendered)

    def test_without_postgres_the_requirement_is_stated(self):
        self.assertIn("PostgreSQL", self._render(None))


class WorkbenchCallbackTests(unittest.TestCase):
    def setUp(self):
        app = dash.Dash(__name__, suppress_callback_exceptions=True)
        workbench_callbacks.register_workbench_callbacks(app)
        self.app = app

    def _runtime(self, board=None, tape=None, error=None, postgres=True):
        if not postgres:
            runtime = SimpleNamespace(unit_of_work_factory=None)
        else:
            repository = mock.MagicMock()
            repository.board.return_value = list(board or [])
            if error:
                repository.tape.side_effect = error
            else:
                repository.tape.return_value = tape
            uow = mock.MagicMock()
            uow.__enter__ = lambda _self: SimpleNamespace(workbench=repository)
            uow.__exit__ = lambda *a: False
            runtime = SimpleNamespace(unit_of_work_factory=lambda: uow)
            self.repository = repository

        patcher = mock.patch.object(
            workbench_callbacks, "get_persistence_runtime", lambda: runtime
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _load(self, symbol=None, selected=None):
        return dash_callback(self.app, "workbench-selection.options")(
            0, 0, symbol, selected
        )

    def _render(self, decision_id):
        return dash_callback(self.app, "workbench-tape.children")(decision_id)

    def test_recent_decisions_are_offered_with_their_state(self):
        self._runtime(board=[_tape()])

        options, value = self._load()

        self.assertIn("NVDA", options[0]["label"])
        self.assertIn("SUCCEEDED", options[0]["label"])
        self.assertEqual(value, "decision-1")

    def test_a_halted_decision_is_marked_in_the_list(self):
        ledger = GateLedger(
            decision_id="decision-1", symbol="NVDA", requested_notional=1_000.0
        )
        ledger.blocked("safety", reasons=["breaker"])
        self._runtime(board=[_tape(ledger=ledger)])

        options, _value = self._load()

        self.assertIn("halted at prepare", options[0]["label"])

    def test_the_symbol_filter_reaches_the_query(self):
        self._runtime(board=[])

        self._load(symbol="NVDA")

        self.repository.board.assert_called_once_with(limit=50, symbol="NVDA")

    def test_a_blank_filter_is_sent_as_no_filter(self):
        self._runtime(board=[])

        self._load(symbol="")

        self.repository.board.assert_called_once_with(limit=50, symbol=None)

    def test_an_existing_selection_survives_a_refresh(self):
        self._runtime(
            board=[_tape(), _tape(summary=_summary(decision_id="decision-2"))]
        )

        _options, value = self._load(selected="decision-2")

        self.assertEqual(value, "decision-2")

    def test_a_selection_that_no_longer_exists_falls_back_to_the_first(self):
        self._runtime(board=[_tape()])

        _options, value = self._load(selected="gone")

        self.assertEqual(value, "decision-1")

    def test_without_postgres_no_decisions_are_offered(self):
        self._runtime(postgres=False)

        self.assertEqual(self._load(), ([], None))

    def test_selecting_a_decision_renders_its_tape_and_charts(self):
        self._runtime(tape=_tape(ledger=_cleared_ledger()))

        body, rail, waterfall, evidence, outcomes = self._render("decision-1")

        self.assertIn("NVDA", str(body))
        self.assertIn("Gather", str(rail))
        self.assertEqual(list(waterfall.data[0].x)[0], "Requested")
        self.assertTrue(evidence.data)
        self.assertTrue(outcomes.layout.annotations)

    def test_nothing_selected_renders_a_prompt_rather_than_an_error(self):
        self._runtime()

        body, _rail, waterfall, _evidence, _outcomes = self._render(None)

        self.assertIn("No decision selected", str(body))
        self.assertIn("Select a decision", waterfall.layout.annotations[0].text)

    def test_a_failing_lookup_reports_the_reason(self):
        self._runtime(error=RuntimeError("connection reset"))

        body, *_rest = self._render("decision-1")

        self.assertIn("Unable to load decision", str(body))
        self.assertIn("connection reset", str(body))

    def test_without_postgres_the_requirement_is_stated(self):
        self._runtime(postgres=False)

        body, *_rest = self._render("decision-1")

        self.assertIn("PostgreSQL", str(body))


if __name__ == "__main__":
    unittest.main()
