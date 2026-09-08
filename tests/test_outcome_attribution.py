from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tradingagents.evaluation import (
    EvaluationHorizon,
    EvaluationRepository,
    FilledEpisodeAttributor,
    OutcomeAttributor,
    PriceObservation,
)
from tradingagents.evaluation.point_in_time import PointInTimeViolation
from tradingagents.execution.order_ledger import BrokerOrderRecord


class Orders:
    def __init__(self, rows):
        self.rows = rows

    def orders_for_decision(self, decision_id):
        return [row for row in self.rows if row.decision_id == decision_id]


class Prices:
    def __init__(self, entry_at):
        self.entry_at = entry_at
        self.before_calls = 0
        self.after = {
            "AAPL": (118.25, entry_at + timedelta(days=1)),
            "SPY": (505.0, entry_at + timedelta(days=1)),
        }

    def price_at_or_before(self, symbol, at):
        self.before_calls += 1
        return PriceObservation(
            symbol=symbol,
            price=500,
            observed_at=at - timedelta(minutes=1),
        )

    def price_at_or_after(self, symbol, at):
        price, observed_at = self.after[symbol]
        return PriceObservation(
            symbol=symbol,
            price=price,
            observed_at=observed_at,
        )


def _fill(decision_id, quantity, price, updated_at):
    return BrokerOrderRecord(
        order_key=f"{decision_id}-{quantity}",
        decision_id=decision_id,
        leg_index=int(quantity),
        broker="test",
        broker_order_id=f"broker-{quantity}",
        client_order_id=f"client-{quantity}",
        symbol="AAPL",
        side="buy",
        status="filled",
        requested_quantity=quantity,
        filled_quantity=quantity,
        filled_avg_price=price,
        submitted_at=updated_at - timedelta(minutes=1),
        updated_at=updated_at,
        terminal_at=updated_at,
    )


def test_fill_episode_and_due_outcome_are_idempotently_attributed(tmp_path) -> None:
    entry_at = datetime(2026, 9, 1, 15, tzinfo=timezone.utc)
    repository = EvaluationRepository(tmp_path / "evaluation.sqlite3")
    prices = Prices(entry_at)
    fills = Orders(
        [
            _fill("decision-1", 1, 100, entry_at - timedelta(seconds=1)),
            _fill("decision-1", 3, 110, entry_at),
        ]
    )
    episode_attributor = FilledEpisodeAttributor(fills, repository, prices)

    episode = episode_attributor.capture(
        "decision-1", action="BUY", confidence=0.8, experiment_id="gpt-5"
    )
    repeated = episode_attributor.capture("decision-1", action="BUY")

    assert episode.reference_price == 107.5
    assert episode.metadata["filled_quantity"] == 4
    assert repeated == episode
    assert prices.before_calls == 1

    attributor = OutcomeAttributor(
        repository,
        prices,
        [EvaluationHorizon("1d", timedelta(days=1), estimated_cost_pct=0.2)],
    )
    outcomes = attributor.resolve_due(now=entry_at + timedelta(days=1, minutes=1))

    assert len(outcomes) == 1
    assert outcomes[0].asset_return_pct == pytest.approx(9.8)
    assert outcomes[0].benchmark_return_pct == pytest.approx(1.0)
    assert attributor.resolve_due(now=entry_at + timedelta(days=2)) == []


def test_capture_rejects_nonterminal_or_risk_reducing_orders(tmp_path) -> None:
    entry_at = datetime(2026, 9, 1, 15, tzinfo=timezone.utc)
    repository = EvaluationRepository(tmp_path / "evaluation.sqlite3")
    pending = _fill("decision-2", 1, 100, entry_at).model_copy(
        update={"status": "partially_filled"}
    )
    attributor = FilledEpisodeAttributor(Orders([pending]), repository, Prices(entry_at))

    with pytest.raises(ValueError, match="no terminal entry fills"):
        attributor.capture("decision-2", action="BUY")
    with pytest.raises(ValueError, match="exposure-adding"):
        attributor.capture("decision-2", action="CLOSE")


def test_outcome_observation_cannot_precede_horizon(tmp_path) -> None:
    entry_at = datetime(2026, 9, 1, 15, tzinfo=timezone.utc)
    repository = EvaluationRepository(tmp_path / "evaluation.sqlite3")
    prices = Prices(entry_at)
    FilledEpisodeAttributor(
        Orders([_fill("decision-3", 1, 100, entry_at)]), repository, prices
    ).capture("decision-3", action="BUY")
    prices.after["AAPL"] = (101, entry_at + timedelta(hours=23))

    with pytest.raises(PointInTimeViolation, match="precedes its horizon"):
        OutcomeAttributor(
            repository, prices, [EvaluationHorizon("1d", timedelta(days=1))]
        ).resolve_due(now=entry_at + timedelta(days=2))
