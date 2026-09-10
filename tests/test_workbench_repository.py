"""Tests for the workbench read model.

The analysis half of a decision and its execution half are written by
different processes at different times. This is where they meet, on
decision_id, and the join has to hold when either half is missing — a
decision blocked before it reached a broker still has an analysis, and a
manual trade has execution with no analysis at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine

from tradingagents.execution.gates import GateLedger
from tradingagents.lifecycle.models import LifecycleStatus
from tradingagents.persistence.postgres import (
    Base,
    PostgresUnitOfWork,
    create_session_factory,
)
from tradingagents.persistence.postgres.models import (
    BrokerOrderRow,
    EvaluationOutcomeRow,
    LifecycleRow,
)
from tradingagents.workbench.tape import StageState

NOW = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


def _lifecycle(session_factory, decision_id="decision-1", symbol="NVDA",
               status=LifecycleStatus.SUCCEEDED, offset=0):
    with PostgresUnitOfWork(session_factory) as uow:
        uow.session.add(
            LifecycleRow(
                decision_id=decision_id,
                symbol=symbol,
                status=status.value,
                idempotency_key=f"key-{decision_id}",
                created_at=NOW + timedelta(minutes=offset),
                updated_at=NOW + timedelta(minutes=offset),
            )
        )
        uow.commit()


def _analysis_payload(decision_id="decision-1", claims=1):
    return {
        "run_id": "run-1",
        "decision_id": decision_id,
        "symbol": "NVDA",
        "trade_date": "2026-09-09",
        "final_signal": "BUY",
        "gather": {"symbol": "NVDA", "trade_date": "2026-09-09", "provenance": {}},
        "analyze": {"produced": 5, "expected": 5, "reports": []},
        "compute": {
            "available": True,
            "scoreboard": {"net_direction": "bullish", "net_confidence": "medium"},
            "sources": [
                {
                    "label": "Market",
                    "claims": [
                        {
                            "claim_id": f"market_{index:03d}",
                            "claim": "Momentum is positive.",
                            "direction": "bullish",
                            "confidence": 0.5,
                            "freshness": 0.6,
                            "numeric_support": 0.0,
                            "contradiction": 0.0,
                        }
                        for index in range(claims)
                    ],
                }
            ],
            "totals": {"claims": claims},
        },
        "decide": {
            "research_debate": {"rounds": 2},
            "risk_debate": {"rounds": 3},
            "final_decision": "FINAL TRANSACTION PROPOSAL: **BUY**",
            "recommended_action": "BUY",
        },
    }


def _record_analysis(session_factory, decision_id="decision-1", **kwargs):
    with PostgresUnitOfWork(session_factory) as uow:
        uow.journal.append(
            "analysis_stages_recorded",
            symbol="NVDA",
            decision_id=decision_id,
            run_id="run-1",
            payload={"analysis": _analysis_payload(decision_id, **kwargs)},
        )
        uow.commit()


def _record_ledger(session_factory, decision_id="decision-1", *, blocked=None,
                   clipped_to=None):
    ledger = GateLedger(
        decision_id=decision_id, symbol="NVDA", requested_notional=1_000.0
    )
    ledger.passed("intent")
    if clipped_to is not None:
        ledger.clipped(
            "risk_sizing", notional_before=1_000.0, notional_after=clipped_to
        )
    if blocked:
        ledger.blocked(blocked, reasons=["a stated reason"])
    else:
        ledger.passed("submission")
    with PostgresUnitOfWork(session_factory) as uow:
        uow.journal.append(
            "gate_ledger_recorded",
            symbol="NVDA",
            decision_id=decision_id,
            run_id="run-1",
            payload={"gate_ledger": ledger.model_dump(mode="json")},
        )
        uow.commit()
    return ledger


def _record_order(session_factory, decision_id="decision-1"):
    with PostgresUnitOfWork(session_factory) as uow:
        uow.session.add(
            BrokerOrderRow(
                order_key=f"{decision_id}-0",
                decision_id=decision_id,
                leg_index=0,
                broker="alpaca",
                broker_order_id="broker-order-1",
                client_order_id=f"{decision_id}-0",
                symbol="NVDA",
                side="buy",
                status="filled",
                requested_quantity=10.0,
                requested_notional=1_000.0,
                filled_quantity=10.0,
                filled_avg_price=100.0,
                submitted_at=NOW,
                updated_at=NOW,
            )
        )
        uow.commit()


def _record_outcome(session_factory, decision_id="decision-1", horizon="1d",
                    excess=1.5, correct=True):
    with PostgresUnitOfWork(session_factory) as uow:
        uow.session.add(
            EvaluationOutcomeRow(
                decision_id=decision_id,
                horizon=horizon,
                outcome_at=NOW + timedelta(days=1),
                asset_price=101.0,
                benchmark_price=100.0,
                asset_return_pct=excess,
                benchmark_return_pct=0.0,
                excess_return_pct=excess,
                directionally_correct=correct,
                estimated_cost_pct=0.1,
            )
        )
        uow.commit()


def _tape(session_factory, decision_id="decision-1"):
    with PostgresUnitOfWork(session_factory) as uow:
        return uow.workbench.tape(decision_id)


def _board(session_factory, **kwargs):
    with PostgresUnitOfWork(session_factory) as uow:
        return uow.workbench.board(**kwargs)


def test_both_halves_of_a_decision_join_on_its_id(session_factory) -> None:
    _lifecycle(session_factory)
    _record_analysis(session_factory)
    _record_ledger(session_factory)
    _record_order(session_factory)
    _record_outcome(session_factory)

    tape = _tape(session_factory)

    assert tape.symbol == "NVDA"
    assert tape.stage("compute").state is StageState.DONE
    assert tape.stage("prepare").state is StageState.DONE
    assert tape.stage("order").state is StageState.DONE
    assert tape.stage("react").state is StageState.DONE


def test_the_analysis_record_is_read_back_intact(session_factory) -> None:
    _lifecycle(session_factory)
    _record_analysis(session_factory, claims=3)

    tape = _tape(session_factory)

    assert tape.run_id == "run-1"
    assert tape.final_signal == "BUY"
    assert len(tape.claim_rows()) == 3


def test_the_gate_ledger_is_read_back_intact(session_factory) -> None:
    _lifecycle(session_factory)
    _record_ledger(session_factory, clipped_to=600.0)

    tape = _tape(session_factory)

    assert tape.gate_ledger.final_notional == 600.0
    assert [step["kind"] for step in tape.gate_ledger.waterfall()] == [
        "start",
        "clip",
        "end",
    ]


def test_the_latest_record_of_each_kind_wins(session_factory) -> None:
    """A re-run appends rather than overwrites; the tape shows the newest."""
    _lifecycle(session_factory)
    _record_analysis(session_factory, claims=1)
    _record_analysis(session_factory, claims=4)

    assert len(_tape(session_factory).claim_rows()) == 4


def test_a_decision_blocked_before_the_broker_still_has_its_analysis(
    session_factory,
) -> None:
    _lifecycle(session_factory, status=LifecycleStatus.BLOCKED)
    _record_analysis(session_factory)
    _record_ledger(session_factory, blocked="safety")

    tape = _tape(session_factory)

    assert tape.stage("decide").state is StageState.DONE
    assert tape.stage("prepare").state is StageState.BLOCKED
    assert tape.stage("order").state is StageState.SKIPPED
    assert tape.halted_at == "prepare"


def test_execution_with_no_recorded_analysis_still_builds_a_tape(
    session_factory,
) -> None:
    """A manual trade never went through the graph."""
    _lifecycle(session_factory)
    _record_ledger(session_factory)
    _record_order(session_factory)

    tape = _tape(session_factory)

    assert tape.stage("gather").state is StageState.PENDING
    assert tape.stage("order").state is StageState.DONE


def test_an_analysis_with_no_execution_still_builds_a_tape(session_factory) -> None:
    _lifecycle(session_factory, status=LifecycleStatus.RECEIVED)
    _record_analysis(session_factory)

    tape = _tape(session_factory)

    assert tape.stage("decide").state is StageState.DONE
    assert tape.stage("prepare").state is StageState.PENDING
    assert tape.current_stage == "decide"


def test_the_timeline_events_are_stamped_with_their_stage(session_factory) -> None:
    _lifecycle(session_factory)
    _record_ledger(session_factory)
    _record_order(session_factory)

    stages = {event.stage for event in _tape(session_factory).events}

    assert "prepare" in stages
    assert "order" in stages


def test_an_unknown_decision_is_reported(session_factory) -> None:
    with pytest.raises(KeyError):
        _tape(session_factory, "no-such-decision")


def test_the_board_returns_recent_decisions_newest_first(session_factory) -> None:
    _lifecycle(session_factory, decision_id="older", offset=0)
    _lifecycle(session_factory, decision_id="newer", offset=5)

    board = _board(session_factory)

    assert [tape.decision_id for tape in board] == ["newer", "older"]


def test_the_board_carries_enough_to_place_each_card(session_factory) -> None:
    _lifecycle(session_factory)
    _record_analysis(session_factory)
    _record_ledger(session_factory, blocked="risk_sizing")

    card = _board(session_factory)[0]

    assert card.current_stage == "prepare"
    assert card.halted_at == "prepare"
    assert "a stated reason" in card.stage("prepare").headline


def test_the_board_filters_by_symbol(session_factory) -> None:
    _lifecycle(session_factory, decision_id="nvda-1", symbol="NVDA")
    _lifecycle(session_factory, decision_id="aapl-1", symbol="AAPL")

    board = _board(session_factory, symbol="AAPL")

    assert [tape.symbol for tape in board] == ["AAPL"]


def test_the_board_filters_by_status(session_factory) -> None:
    _lifecycle(session_factory, decision_id="done-1", status=LifecycleStatus.SUCCEEDED)
    _lifecycle(session_factory, decision_id="stopped-1", status=LifecycleStatus.BLOCKED)

    board = _board(session_factory, status="blocked")

    assert [tape.decision_id for tape in board] == ["stopped-1"]


def test_an_empty_board_is_not_an_error(session_factory) -> None:
    assert _board(session_factory) == []


def test_the_board_respects_its_limit(session_factory) -> None:
    for index in range(5):
        _lifecycle(session_factory, decision_id=f"decision-{index}", offset=index)

    assert len(_board(session_factory, limit=2)) == 2


def _episode(session_factory, decision_id, *, experiment_id, replay=False,
             offset=0, symbol="NVDA"):
    from tradingagents.evaluation.models import EvaluationEpisode
    from tradingagents.workbench.replay import REPLAY_SOURCE

    metadata = {"experiment_role": "shadow"}
    if replay:
        metadata["origin"] = REPLAY_SOURCE
        metadata["origin_decision_id"] = "decision-1"
    with PostgresUnitOfWork(session_factory) as uow:
        uow.evaluation.record_episode(
            EvaluationEpisode(
                decision_id=decision_id,
                symbol=symbol,
                action="BUY",
                decision_at=NOW + timedelta(minutes=offset),
                data_as_of=NOW + timedelta(minutes=offset - 1),
                reference_price=100.0,
                benchmark_symbol="SPY",
                benchmark_price=500.0,
                experiment_id=experiment_id,
                metadata=metadata,
            )
        )
        uow.commit()


def _outcome_for(session_factory, decision_id, *, excess=1.5, horizon="1d"):
    from tradingagents.evaluation.models import EvaluationOutcome

    with PostgresUnitOfWork(session_factory) as uow:
        uow.evaluation.record_outcome(
            EvaluationOutcome(
                decision_id=decision_id,
                horizon=horizon,
                outcome_at=NOW + timedelta(days=1),
                asset_price=101.0,
                benchmark_price=500.0,
                asset_return_pct=excess,
                benchmark_return_pct=0.0,
                excess_return_pct=excess,
                directionally_correct=excess > 0,
                estimated_cost_pct=0.1,
            )
        )
        uow.commit()


def test_replayed_outcomes_are_separable_from_live_ones(session_factory) -> None:
    """A challenger built out of replays is not evidence to promote on, so
    the gate has to be able to leave them out."""
    _episode(session_factory, "live-1", experiment_id="deep", offset=0)
    _outcome_for(session_factory, "live-1")
    _episode(session_factory, "replay-1", experiment_id="deep", replay=True, offset=1)
    _outcome_for(session_factory, "replay-1")

    with PostgresUnitOfWork(session_factory) as uow:
        everything = uow.evaluation.outcomes(experiment_id="deep")
        live_only = uow.evaluation.outcomes(
            experiment_id="deep", include_replays=False
        )
        uow.rollback()

    assert {row.decision_id for row in everything} == {"live-1", "replay-1"}
    assert {row.decision_id for row in live_only} == {"live-1"}


def test_a_ledger_with_no_replays_is_unaffected_by_the_filter(session_factory) -> None:
    _episode(session_factory, "live-1", experiment_id="deep")
    _outcome_for(session_factory, "live-1")

    with PostgresUnitOfWork(session_factory) as uow:
        live_only = uow.evaluation.outcomes(
            experiment_id="deep", include_replays=False
        )
        uow.rollback()

    assert len(live_only) == 1


def test_a_replay_reaches_the_promotion_gate(session_factory) -> None:
    """End to end: a replayed episode resolves into an outcome the gate
    compares against the champion."""
    from tradingagents.evaluation import PromotionStatus
    from tradingagents.workbench.promotion_view import build_promotion_view

    for index in range(4):
        _episode(
            session_factory,
            f"replay-{index}",
            experiment_id="deep",
            replay=True,
            offset=index,
        )
        _outcome_for(session_factory, f"replay-{index}", excess=3.0)
        _episode(
            session_factory,
            f"champion-{index}",
            experiment_id="champion",
            offset=index,
        )
        _outcome_for(session_factory, f"champion-{index}", excess=0.1)

    with PostgresUnitOfWork(session_factory) as uow:
        included = build_promotion_view(
            uow,
            challenger="deep",
            champion="champion",
            horizon="1d",
            include_replays=True,
            policy=None,
        )
        excluded = build_promotion_view(
            uow,
            challenger="deep",
            champion="champion",
            horizon="1d",
            include_replays=False,
        )
        uow.rollback()

    assert included.decision.challenger.count == 4
    assert included.challenger_replays == 4
    assert included.replay_share_pct == 100.0
    assert "came from replays" in " ".join(included.notes)

    # With replays excluded the challenger has nothing to stand on.
    assert excluded.decision.challenger.count == 0
    assert excluded.decision.status is PromotionStatus.INSUFFICIENT_DATA
