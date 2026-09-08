"""Create evaluation episodes from fills and resolve their future outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol

from pydantic import BaseModel, Field

from tradingagents.execution.order_ledger import OrderLedgerPort
from tradingagents.persistence.protocols import EvaluationRepositoryPort

from .models import EvaluationEpisode, EvaluationOutcome
from .outcomes import calculate_outcome
from .point_in_time import PointInTimeViolation, ensure_aware


class PriceObservation(BaseModel):
    symbol: str
    price: float = Field(gt=0)
    observed_at: datetime


class HistoricalPriceProvider(Protocol):
    def price_at_or_before(self, symbol: str, at: datetime) -> PriceObservation: ...

    def price_at_or_after(self, symbol: str, at: datetime) -> PriceObservation: ...


@dataclass(frozen=True)
class EvaluationHorizon:
    name: str
    after: timedelta
    estimated_cost_pct: float = 0.0

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("horizon name is required")
        if self.after <= timedelta(0):
            raise ValueError("horizon duration must be positive")
        if self.estimated_cost_pct < 0:
            raise ValueError("estimated cost cannot be negative")


class FilledEpisodeAttributor:
    """Use actual terminal fills as the evaluation entry reference."""

    def __init__(
        self,
        orders: OrderLedgerPort,
        evaluation: EvaluationRepositoryPort,
        prices: HistoricalPriceProvider,
    ):
        self.orders = orders
        self.evaluation = evaluation
        self.prices = prices

    def capture(
        self,
        decision_id: str,
        *,
        action: str,
        benchmark_symbol: str = "SPY",
        confidence: Optional[float] = None,
        experiment_id: str = "default",
        metadata: Optional[dict] = None,
    ) -> EvaluationEpisode:
        existing = self.evaluation.get_episode(decision_id)
        if existing is not None:
            return existing
        action_key = action.upper().strip()
        expected_side = "sell" if action_key == "SHORT" else "buy"
        if action_key not in {"BUY", "OPEN", "INCREASE", "LONG", "SHORT"}:
            raise ValueError("only exposure-adding decisions can create fill episodes")
        fills = [
            row
            for row in self.orders.orders_for_decision(decision_id)
            if row.side.lower() == expected_side
            and row.filled_quantity > 0
            and row.filled_avg_price is not None
            and row.status.lower() in {"filled", "canceled", "expired"}
        ]
        if not fills:
            raise ValueError(f"decision {decision_id} has no terminal entry fills")
        symbols = {row.symbol.upper().replace("/", "") for row in fills}
        if len(symbols) != 1:
            raise ValueError("one evaluation episode cannot combine multiple symbols")
        quantity = sum(row.filled_quantity for row in fills)
        reference_price = sum(
            row.filled_quantity * float(row.filled_avg_price) for row in fills
        ) / quantity
        entry_at = max(row.terminal_at or row.updated_at for row in fills)
        ensure_aware(entry_at, "entry_at")
        benchmark = self.prices.price_at_or_before(benchmark_symbol, entry_at)
        ensure_aware(benchmark.observed_at, "benchmark observed_at")
        if benchmark.observed_at.astimezone(timezone.utc) > entry_at.astimezone(
            timezone.utc
        ):
            raise PointInTimeViolation("benchmark reference observation is after entry")
        episode = EvaluationEpisode(
            decision_id=decision_id,
            symbol=fills[0].symbol,
            action=action_key,
            decision_at=entry_at,
            data_as_of=benchmark.observed_at,
            reference_price=reference_price,
            benchmark_symbol=benchmark_symbol,
            benchmark_price=benchmark.price,
            confidence=confidence,
            experiment_id=experiment_id,
            metadata={
                **(metadata or {}),
                "source": "terminal_fills",
                "filled_quantity": quantity,
                "fill_count": len(fills),
            },
        )
        self.evaluation.record_episode(episode)
        return episode


class OutcomeAttributor:
    def __init__(
        self,
        evaluation: EvaluationRepositoryPort,
        prices: HistoricalPriceProvider,
        horizons: list[EvaluationHorizon],
    ):
        names = [row.name for row in horizons]
        if len(names) != len(set(names)):
            raise ValueError("evaluation horizon names must be unique")
        self.evaluation = evaluation
        self.prices = prices
        self.horizons = tuple(horizons)

    def resolve_due(self, *, now: Optional[datetime] = None) -> list[EvaluationOutcome]:
        now = now or datetime.now(timezone.utc)
        ensure_aware(now, "now")
        resolved = []
        for horizon in self.horizons:
            due_before = now - horizon.after
            for episode in self.evaluation.pending_episodes(
                horizon=horizon.name, due_before=due_before
            ):
                outcome = attribute_episode_outcome(
                    episode,
                    horizon=horizon,
                    prices=self.prices,
                    now=now,
                )
                self.evaluation.record_outcome(outcome)
                resolved.append(outcome)
        return resolved


def attribute_episode_outcome(
    episode: EvaluationEpisode,
    *,
    horizon: EvaluationHorizon,
    prices: HistoricalPriceProvider,
    now: datetime,
) -> EvaluationOutcome:
    ensure_aware(now, "now")
    target = episode.decision_at + horizon.after
    asset = prices.price_at_or_after(episode.symbol, target)
    benchmark = prices.price_at_or_after(episode.benchmark_symbol, target)
    _validate_outcome_observation(asset, target, now)
    _validate_outcome_observation(benchmark, target, now)
    return calculate_outcome(
        episode,
        horizon=horizon.name,
        outcome_at=max(asset.observed_at, benchmark.observed_at),
        asset_price=asset.price,
        benchmark_price=benchmark.price,
        estimated_cost_pct=horizon.estimated_cost_pct,
    )


def _validate_outcome_observation(
    observation: PriceObservation, target: datetime, now: datetime
) -> None:
    ensure_aware(observation.observed_at, "price observed_at")
    observed = observation.observed_at.astimezone(timezone.utc)
    if observed < target.astimezone(timezone.utc):
        raise PointInTimeViolation("outcome observation precedes its horizon")
    if observed > now.astimezone(timezone.utc):
        raise PointInTimeViolation("outcome observation is in the future")
