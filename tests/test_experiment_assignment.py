from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tradingagents.evaluation import (
    DeterministicExperimentAssigner,
    ExperimentVariant,
    PriceObservation,
    build_signal_episode,
)


def test_assignment_is_stable_and_uses_weighted_variants() -> None:
    assigner = DeterministicExperimentAssigner(
        [
            ExperimentVariant(
                experiment_id="champion", weight=9, execution_eligible=True
            ),
            ExperimentVariant(experiment_id="challenger", weight=1),
        ],
        seed="release-1",
    )

    first = assigner.assign("MSFT:2026-09-08")
    assert assigner.assign("MSFT:2026-09-08") == first
    assigned = {
        assigner.assign(f"SYMBOL-{index}:2026-09-08").experiment_id
        for index in range(100)
    }
    assert assigned == {"champion", "challenger"}


def test_signal_episode_uses_only_prices_available_at_decision_time() -> None:
    decision_at = datetime(2026, 9, 8, 15, tzinfo=timezone.utc)

    class Prices:
        def price_at_or_before(self, symbol, at):
            return PriceObservation(
                symbol=symbol,
                price=100 if symbol == "MSFT" else 500,
                observed_at=at - timedelta(minutes=1),
            )

    episode = build_signal_episode(
        "shadow-decision",
        symbol="MSFT",
        action="BUY",
        decision_at=decision_at,
        prices=Prices(),
        experiment_id="challenger",
    )

    assert episode.experiment_id == "challenger"
    assert episode.reference_price == 100
    assert episode.metadata["source"] == "shadow_signal"
