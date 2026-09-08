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


class EntryFill(BaseModel):
    symbol: str
    side: str
    quantity: float = Field(gt=0)
    average_price: float = Field(gt=0)
    observed_at: datetime


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
        return build_filled_episode(
            decision_id,
            [
                EntryFill(
                    symbol=row.symbol,
                    side=row.side,
                    quantity=row.filled_quantity,
                    average_price=float(row.filled_avg_price),
                    observed_at=row.terminal_at or row.updated_at,
                )
                for row in fills
            ],
            action=action_key,
            prices=self.prices,
            benchmark_symbol=benchmark_symbol,
            confidence=confidence,
            experiment_id=experiment_id,
            metadata=metadata,
            persist=self.evaluation.record_episode,
        )


def build_filled_episode(
    decision_id: str,
    fills: list[EntryFill],
    *,
    action: str,
    prices: HistoricalPriceProvider,
    benchmark_symbol: str = "SPY",
    confidence: Optional[float] = None,
    experiment_id: str = "default",
    metadata: Optional[dict] = None,
    persist=None,
) -> EvaluationEpisode:
    if not fills:
        raise ValueError(f"decision {decision_id} has no terminal entry fills")
    action_key = action.upper().strip()
    if action_key not in {"BUY", "OPEN", "INCREASE", "LONG", "SHORT"}:
        raise ValueError("only exposure-adding decisions can create fill episodes")
    expected_side = "sell" if action_key == "SHORT" else "buy"
    if any(fill.side.lower() != expected_side for fill in fills):
        raise ValueError("entry fill side does not match evaluation action")
    symbols = {row.symbol.upper().replace("/", "") for row in fills}
    if len(symbols) != 1:
        raise ValueError("one evaluation episode cannot combine multiple symbols")
    quantity = sum(row.quantity for row in fills)
    reference_price = sum(
        row.quantity * row.average_price for row in fills
    ) / quantity
    entry_at = max(row.observed_at for row in fills)
    ensure_aware(entry_at, "entry_at")
    benchmark = prices.price_at_or_before(benchmark_symbol, entry_at)
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
    if persist is not None:
        persist(episode)
    return episode


def build_signal_episode(
    decision_id: str,
    *,
    symbol: str,
    action: str,
    decision_at: datetime,
    prices: HistoricalPriceProvider,
    benchmark_symbol: str = "SPY",
    confidence: Optional[float] = None,
    experiment_id: str,
    metadata: Optional[dict] = None,
    persist=None,
) -> EvaluationEpisode:
    """Capture a point-in-time episode for a decision that never reaches a broker."""
    ensure_aware(decision_at, "decision_at")
    action_key = action.upper().strip()
    if action_key not in {"BUY", "OPEN", "INCREASE", "LONG", "SHORT"}:
        raise ValueError("only exposure-adding signals can create shadow episodes")
    asset = prices.price_at_or_before(symbol, decision_at)
    benchmark = prices.price_at_or_before(benchmark_symbol, decision_at)
    for observation in (asset, benchmark):
        ensure_aware(observation.observed_at, "price observed_at")
        if observation.observed_at.astimezone(timezone.utc) > decision_at.astimezone(
            timezone.utc
        ):
            raise PointInTimeViolation("signal reference observation is after decision")
    episode = EvaluationEpisode(
        decision_id=decision_id,
        symbol=symbol,
        action=action_key,
        decision_at=decision_at,
        data_as_of=max(asset.observed_at, benchmark.observed_at),
        reference_price=asset.price,
        benchmark_symbol=benchmark_symbol,
        benchmark_price=benchmark.price,
        confidence=confidence,
        experiment_id=experiment_id,
        metadata={**(metadata or {}), "source": "shadow_signal"},
    )
    if persist is not None:
        persist(episode)
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
