"""Tests for the pipeline board and the vitals strip.

The board replaces the agent status table. The table showed which agent was
running for the symbols this process happened to be holding; the board
shows every decision, live and persisted, in the column of the stage it
reached — and, when one has stopped, the gate that stopped it.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import dash

from conftest import dash_callback
from tradingagents.execution.gates import GateLedger
from tradingagents.workbench.board import (
    card_from_tape,
    group_by_stage,
    halt_counts,
    live_cards,
    merge_cards,
    stage_counts,
    throughput_series,
)
from tradingagents.workbench.tape import STAGES, build_tape
from webui.callbacks import board_callbacks
from webui.components.pipeline_board import (
    board_card,
    board_column,
    halt_breakdown_figure,
    stage_distribution_figure,
    throughput_figure,
)
from webui.utils.state import AppState

NOW = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)


def _summary(**overrides):
    fields = {
        "decision_id": "decision-1",
        "symbol": "NVDA",
        "status": "succeeded",
        "created_at": NOW,
        "updated_at": NOW,
        "broker": "alpaca",
        "order_count": 1,
        "filled_quantity": 10.0,
        "error": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _blocked_ledger(gate="safety", reason="daily loss breaker tripped"):
    ledger = GateLedger(
        decision_id="decision-1", symbol="NVDA", requested_notional=1_000.0
    )
    ledger.blocked(gate, reasons=[reason])
    return ledger


def _tape(**kwargs):
    return build_tape(summary=kwargs.pop("summary", _summary()), **kwargs)


def _statuses(**overrides):
    statuses = {
        "Market Analyst": "pending",
        "Social Analyst": "pending",
        "News Analyst": "pending",
        "Fundamentals Analyst": "pending",
        "Macro Analyst": "pending",
        "Bull Researcher": "pending",
        "Bear Researcher": "pending",
        "Research Manager": "pending",
        "Trader": "pending",
        "Risky Analyst": "pending",
        "Safe Analyst": "pending",
        "Neutral Analyst": "pending",
        "Portfolio Manager": "pending",
    }
    statuses.update(overrides)
    return {"agent_statuses": statuses}


class PersistedCardTests(unittest.TestCase):
    def test_a_finished_decision_sits_at_its_furthest_stage(self):
        card = card_from_tape(
            _tape(orders=[{"filled_quantity": 10.0, "status": "filled"}])
        )

        self.assertEqual(card["stage"], "order")
        self.assertEqual(card["symbol"], "NVDA")
        self.assertFalse(card["live"])

    def test_a_halted_decision_is_marked_in_danger_at_the_stage_that_stopped_it(self):
        card = card_from_tape(
            _tape(summary=_summary(status="blocked"), gate_ledger=_blocked_ledger())
        )

        self.assertEqual(card["stage"], "prepare")
        self.assertEqual(card["badge_color"], "danger")
        self.assertIn("daily loss breaker", card["headline"])

    def test_the_card_carries_the_signal_as_a_hint(self):
        card = card_from_tape(_tape(analysis={"final_signal": "BUY"}))

        self.assertEqual(card["hint"], "BUY")


class LiveCardTests(unittest.TestCase):
    """An analysis in flight has no decision id yet, so the database has
    nothing to show for it."""

    def test_a_running_analyst_places_the_card_at_analyze(self):
        cards = live_cards({"NVDA": _statuses(**{"News Analyst": "in_progress"})})

        self.assertEqual(cards[0]["stage"], "analyze")
        self.assertIn("News Analyst running", cards[0]["headline"])
        self.assertTrue(cards[0]["live"])

    def test_a_running_debate_places_the_card_at_decide(self):
        cards = live_cards({"NVDA": _statuses(**{"Bull Researcher": "in_progress"})})

        self.assertEqual(cards[0]["stage"], "decide")

    def test_the_risk_manager_also_places_the_card_at_decide(self):
        cards = live_cards({"NVDA": _statuses(**{"Portfolio Manager": "in_progress"})})

        self.assertEqual(cards[0]["stage"], "decide")

    def test_a_symbol_that_has_not_started_is_not_on_the_board(self):
        """A queued symbol has not entered the pipeline."""
        self.assertEqual(live_cards({"NVDA": _statuses()}), [])

    def test_a_finished_analyst_stage_advances_the_card(self):
        statuses = _statuses(
            **{
                agent: "completed"
                for agent in (
                    "Market Analyst",
                    "Social Analyst",
                    "News Analyst",
                    "Fundamentals Analyst",
                    "Macro Analyst",
                )
            }
        )

        cards = live_cards({"NVDA": statuses})

        self.assertEqual(cards[0]["stage"], "analyze")
        self.assertEqual(cards[0]["headline"], "Waiting")

    def test_the_card_counts_how_many_agents_are_done(self):
        cards = live_cards(
            {"NVDA": _statuses(**{"Market Analyst": "completed",
                                  "News Analyst": "in_progress"})}
        )

        self.assertIn("1 of 13 agents done", cards[0]["hint"])

    def test_the_symbol_being_analyzed_is_flagged(self):
        cards = live_cards(
            {"NVDA": _statuses(**{"News Analyst": "in_progress"})},
            analyzing_symbol="NVDA",
        )

        self.assertTrue(cards[0]["active"])

    def test_no_run_in_flight_produces_no_live_cards(self):
        self.assertEqual(live_cards({}), [])
        self.assertEqual(live_cards(None), [])


class MergeTests(unittest.TestCase):
    def test_a_persisted_decision_supersedes_its_live_card(self):
        """The database knows about gates and orders; agent state does not."""
        live = live_cards({"NVDA": _statuses(**{"News Analyst": "in_progress"})})
        persisted = [card_from_tape(_tape())]

        merged = merge_cards(live, persisted)

        self.assertEqual(len(merged), 1)
        self.assertFalse(merged[0]["live"])

    def test_a_symbol_with_no_persisted_decision_keeps_its_live_card(self):
        live = live_cards({"AAPL": _statuses(**{"News Analyst": "in_progress"})})
        persisted = [card_from_tape(_tape())]

        merged = merge_cards(live, persisted)

        self.assertEqual([card["symbol"] for card in merged], ["AAPL", "NVDA"])

    def test_live_cards_come_first(self):
        live = live_cards({"AAPL": _statuses(**{"News Analyst": "in_progress"})})

        merged = merge_cards(live, [card_from_tape(_tape())])

        self.assertTrue(merged[0]["live"])


class GroupingTests(unittest.TestCase):
    def test_every_stage_gets_a_column_even_when_empty(self):
        grouped = group_by_stage([])

        self.assertEqual(list(grouped), [key for key, _ in STAGES])

    def test_cards_land_in_their_own_stage(self):
        cards = [card_from_tape(_tape(orders=[{"filled_quantity": 1.0}]))]

        self.assertEqual(len(group_by_stage(cards)["order"]), 1)

    def test_the_counts_match_the_grouping(self):
        cards = [
            card_from_tape(_tape(orders=[{"filled_quantity": 1.0}])),
            card_from_tape(
                _tape(summary=_summary(status="blocked"), gate_ledger=_blocked_ledger())
            ),
        ]

        counts = stage_counts(cards)

        self.assertEqual(counts["order"], 1)
        self.assertEqual(counts["prepare"], 1)
        self.assertEqual(counts["gather"], 0)


class HaltBreakdownTests(unittest.TestCase):
    def test_each_blocking_gate_is_counted_by_name(self):
        tapes = [
            _tape(summary=_summary(status="blocked"), gate_ledger=_blocked_ledger()),
            _tape(summary=_summary(status="blocked"), gate_ledger=_blocked_ledger()),
            _tape(
                summary=_summary(status="blocked"),
                gate_ledger=_blocked_ledger("risk_sizing", "no headroom"),
            ),
        ]

        counts = halt_counts(tapes)

        self.assertEqual(counts["Safety limits"], 2)
        self.assertEqual(counts["Risk sizing"], 1)

    def test_a_clean_run_contributes_nothing(self):
        self.assertEqual(halt_counts([_tape()]), {})


class ThroughputTests(unittest.TestCase):
    def test_every_hour_in_the_window_is_present(self):
        series = throughput_series([], hours=6, now=NOW)

        self.assertEqual(len(series), 6)
        self.assertTrue(all(point["count"] == 0 for point in series))

    def test_decisions_are_counted_into_their_hour(self):
        """Two inside the current hour, one two hours back."""
        tapes = [
            _tape(summary=_summary(created_at=NOW)),
            _tape(summary=_summary(created_at=NOW + timedelta(minutes=20))),
            _tape(summary=_summary(created_at=NOW - timedelta(hours=2))),
        ]

        series = throughput_series(tapes, hours=6, now=NOW + timedelta(minutes=40))

        self.assertEqual(series[-1]["count"], 2)
        self.assertEqual(series[-3]["count"], 1)

    def test_a_decision_older_than_the_window_is_not_counted(self):
        tapes = [_tape(summary=_summary(created_at=NOW - timedelta(days=3)))]

        series = throughput_series(tapes, hours=6, now=NOW)

        self.assertEqual(sum(point["count"] for point in series), 0)

    def test_a_naive_timestamp_is_read_as_utc(self):
        tapes = [_tape(summary=_summary(created_at=NOW.replace(tzinfo=None)))]

        series = throughput_series(tapes, hours=6, now=NOW)

        self.assertEqual(series[-1]["count"], 1)

    def test_a_decision_with_no_timestamp_is_skipped(self):
        tapes = [_tape(summary=_summary(created_at=None))]

        self.assertEqual(
            sum(point["count"] for point in throughput_series(tapes, now=NOW)), 0
        )


class BoardChartTests(unittest.TestCase):
    def test_the_distribution_charts_every_stage(self):
        figure = stage_distribution_figure({"order": 2, "prepare": 1})

        self.assertEqual(len(figure.data[0].x), len(STAGES))

    def test_an_empty_board_says_so(self):
        self.assertIn(
            "No decisions", stage_distribution_figure({}).layout.annotations[0].text
        )

    def test_the_halt_chart_orders_by_frequency(self):
        figure = halt_breakdown_figure({"Safety limits": 1, "Risk sizing": 3})

        self.assertEqual(list(figure.data[0].y), ["Risk sizing", "Safety limits"])

    def test_nothing_stopped_says_so(self):
        self.assertIn("Nothing has been stopped", halt_breakdown_figure({}).layout.annotations[0].text)

    def test_the_throughput_chart_plots_the_window(self):
        figure = throughput_figure(throughput_series([], hours=4, now=NOW))

        self.assertEqual(len(figure.data[0].x), 4)

    def test_no_throughput_says_so(self):
        self.assertIn("No throughput", throughput_figure([]).layout.annotations[0].text)


class BoardRenderTests(unittest.TestCase):
    def test_a_card_renders_its_symbol_and_headline(self):
        rendered = str(board_card(card_from_tape(_tape())))

        self.assertIn("NVDA", rendered)
        self.assertIn("SUCCEEDED", rendered)

    def test_an_empty_column_shows_a_placeholder(self):
        self.assertIn("—", str(board_column("Gather", [])))

    def test_a_column_counts_its_cards(self):
        rendered = str(board_column("Order", [card_from_tape(_tape())]))

        self.assertIn("Order", rendered)
        self.assertIn("1", rendered)


class BoardCallbackTests(unittest.TestCase):
    def setUp(self):
        app = dash.Dash(__name__, suppress_callback_exceptions=True)
        board_callbacks.register_board_callbacks(app)
        self.app = app

        self.state = AppState()
        patcher = mock.patch.object(board_callbacks, "app_state", self.state)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _runtime(self, tapes=None, health=None, error=None, postgres=True):
        if not postgres:
            runtime = SimpleNamespace(unit_of_work_factory=None)
        else:
            workbench = mock.MagicMock()
            if error:
                workbench.board.side_effect = error
            else:
                workbench.board.return_value = list(tapes or [])
            operations = mock.MagicMock()
            operations.health.return_value = health
            uow = mock.MagicMock()
            uow.__enter__ = lambda _self: SimpleNamespace(
                workbench=workbench, operations=operations
            )
            uow.__exit__ = lambda *a: False
            runtime = SimpleNamespace(unit_of_work_factory=lambda: uow)

        patcher = mock.patch.object(
            board_callbacks, "get_persistence_runtime", lambda: runtime
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _board(self):
        return dash_callback(self.app, "pipeline-board.children")(0, 0)

    def _vitals(self):
        return str(dash_callback(self.app, "vitals-strip.children")(0))

    def test_persisted_decisions_are_placed_on_the_board(self):
        self._runtime(tapes=[_tape(orders=[{"filled_quantity": 1.0}])])

        columns, distribution, halts, throughput = self._board()

        self.assertIn("NVDA", str(columns))
        self.assertTrue(distribution.data)
        self.assertTrue(halts.layout.annotations)
        self.assertTrue(throughput.data)

    def test_the_running_analysis_appears_even_before_it_has_a_decision(self):
        self._runtime(tapes=[])
        self.state.init_symbol_state("AAPL")
        self.state.get_state("AAPL")["agent_statuses"]["News Analyst"] = "in_progress"

        columns, *_rest = self._board()

        self.assertIn("AAPL", str(columns))
        self.assertIn("LIVE", str(columns))

    def test_without_postgres_the_board_still_shows_the_running_analysis(self):
        self._runtime(postgres=False)
        self.state.init_symbol_state("AAPL")
        self.state.get_state("AAPL")["agent_statuses"]["News Analyst"] = "in_progress"

        columns, *_rest = self._board()

        self.assertIn("AAPL", str(columns))
        self.assertIn("DATABASE_URL", str(columns))

    def test_a_failing_query_reports_the_reason(self):
        self._runtime(error=RuntimeError("connection reset"))

        columns, *_rest = self._board()

        self.assertIn("Unable to load the board", str(columns))
        self.assertIn("connection reset", str(columns))


def _health(**overrides):
    fields = {
        "controls": [],
        "heartbeats": [],
        "reconciliation_pending": 0,
        "reconciliation_oldest_lag_seconds": 0.0,
        "analyses_in_flight": 0,
        "active_reservation_allocations": 0,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class VitalsTests(BoardCallbackTests):
    def test_an_idle_machine_says_so(self):
        self._runtime(health=_health())

        self.assertIn("Idle", self._vitals())

    def test_each_scheduling_mode_is_named(self):
        self._runtime(health=_health())

        for attribute, expected in (
            ("screener_enabled", "Screener"),
            ("loop_enabled", "Loop"),
            ("market_hour_enabled", "Market hours"),
        ):
            state = AppState()
            setattr(state, attribute, True)
            with mock.patch.object(board_callbacks, "app_state", state):
                self.assertIn(expected, self._vitals(), attribute)

    def test_a_single_run_in_progress_is_named(self):
        self._runtime(health=_health())
        self.state.analysis_running = True

        self.assertIn("Single run", self._vitals())

    def test_the_symbol_being_analyzed_is_shown(self):
        self._runtime(health=_health())
        self.state.analyzing_symbol = "NVDA"

        self.assertIn("NVDA", self._vitals())

    def test_a_paused_scope_is_flagged(self):
        self._runtime(
            health=_health(
                controls=[
                    SimpleNamespace(
                        service="execution:alpaca", paused=True, reason="account drift"
                    )
                ]
            )
        )

        rendered = self._vitals()
        self.assertIn("1 paused", rendered)
        self.assertIn("account drift", rendered)

    def test_a_clear_quarantine_says_clear(self):
        self._runtime(health=_health())

        self.assertIn("clear", self._vitals())

    def test_a_stale_worker_is_flagged(self):
        self._runtime(
            health=_health(
                heartbeats=[
                    SimpleNamespace(service="reconciliation", stale=True),
                    SimpleNamespace(service="evaluation", stale=False),
                ]
            )
        )

        rendered = self._vitals()
        self.assertIn("1/2 live", rendered)
        self.assertIn("reconciliation stale", rendered)

    def test_a_reconciliation_backlog_is_shown_with_its_lag(self):
        self._runtime(
            health=_health(
                reconciliation_pending=4, reconciliation_oldest_lag_seconds=600.0
            )
        )

        rendered = self._vitals()
        self.assertIn("4 pending", rendered)
        self.assertIn("600s behind", rendered)

    def test_without_postgres_the_worker_reading_says_so(self):
        self._runtime(postgres=False)

        self.assertIn("NO DATABASE", self._vitals())

    def test_an_unreachable_database_is_reported_not_hidden(self):
        self._runtime()
        with mock.patch.object(
            board_callbacks, "load_health", side_effect=RuntimeError("timeout")
        ):
            rendered = self._vitals()

        self.assertIn("UNAVAILABLE", rendered)
        self.assertIn("timeout", rendered)

    def test_the_token_budget_is_reported_when_one_is_set(self):
        self._runtime(health=_health())

        with mock.patch(
            "tradingagents.dataflows.config.get_config",
            lambda: {"daily_llm_token_budget": 1_000},
        ):
            with mock.patch(
                "tradingagents.safety.get_safety_guard",
                lambda: SimpleNamespace(llm_tokens_today=900),
            ):
                rendered = self._vitals()

        self.assertIn("100 left", rendered)
        self.assertIn("900 of 1,000 used", rendered)

    def test_no_budget_reads_as_unlimited(self):
        self._runtime(health=_health())

        with mock.patch(
            "tradingagents.dataflows.config.get_config",
            lambda: {"daily_llm_token_budget": 0},
        ):
            self.assertIn("unlimited", self._vitals())

    def test_an_unavailable_budget_guard_does_not_break_the_strip(self):
        self._runtime(health=_health())

        with mock.patch(
            "tradingagents.dataflows.config.get_config",
            side_effect=RuntimeError("config unreadable"),
        ):
            self.assertIn("Token budget", self._vitals())


if __name__ == "__main__":
    unittest.main()
