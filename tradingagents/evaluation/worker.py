"""Recurring worker for due evaluation outcomes and stale reservations."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
from uuid import uuid4

from .attribution import EvaluationHorizon, attribute_episode_outcome
from .price_registry import default_historical_price_registry


LOGGER = logging.getLogger(__name__)


@dataclass
class EvaluationWorkerResult:
    pending: int = 0
    resolved: int = 0
    failed: int = 0
    expired_reservations: int = 0
    errors: list[str] = field(default_factory=list)


class EvaluationWorker:
    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        prices,
        horizons: list[EvaluationHorizon],
        worker_id: Optional[str] = None,
    ):
        self.unit_of_work_factory = unit_of_work_factory
        self.prices = prices
        self.horizons = tuple(horizons)
        self.worker_id = worker_id or f"evaluation-{uuid4()}"

    def run_once(self, *, now: Optional[datetime] = None) -> EvaluationWorkerResult:
        now = now or datetime.now(timezone.utc)
        work = []
        result = EvaluationWorkerResult()
        with self.unit_of_work_factory() as uow:
            for horizon in self.horizons:
                due_before = now - horizon.after
                episodes = uow.evaluation.pending_episodes(
                    horizon=horizon.name,
                    due_before=due_before,
                )
                work.extend((episode, horizon) for episode in episodes)
            reservations = getattr(uow, "portfolio_reservations", None)
            if reservations is not None:
                result.expired_reservations = reservations.expire_due(now)
            uow.commit()
        result.pending = len(work)
        for episode, horizon in work:
            try:
                outcome = attribute_episode_outcome(
                    episode,
                    horizon=horizon,
                    prices=self.prices,
                    now=now,
                )
                with self.unit_of_work_factory() as uow:
                    uow.evaluation.record_outcome(outcome)
                    uow.commit()
                result.resolved += 1
            except Exception as exc:
                result.failed += 1
                message = f"{episode.decision_id}/{horizon.name}: {exc}"
                result.errors.append(message)
                LOGGER.exception("Evaluation attribution failed for %s", message)
        try:
            with self.unit_of_work_factory() as uow:
                operations = getattr(uow, "operations", None)
                if operations is not None:
                    operations.beat(
                        "evaluation-worker",
                        instance_id=self.worker_id,
                        status="degraded" if result.failed else "healthy",
                        details={
                            "pending": result.pending,
                            "resolved": result.resolved,
                            "failed": result.failed,
                            "expired_reservations": result.expired_reservations,
                        },
                        now=now,
                    )
                uow.commit()
        except Exception:
            LOGGER.exception("Failed to record evaluation heartbeat")
        return result

    def run_forever(
        self,
        *,
        interval_seconds: float,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        stop_event = stop_event or threading.Event()
        interval_seconds = max(1.0, float(interval_seconds))
        while not stop_event.is_set():
            result = self.run_once()
            LOGGER.info(
                "evaluation cycle pending=%s resolved=%s failed=%s expired=%s",
                result.pending,
                result.resolved,
                result.failed,
                result.expired_reservations,
            )
            stop_event.wait(interval_seconds)


def _horizons_from_env() -> list[EvaluationHorizon]:
    raw = os.getenv("EVALUATION_HORIZONS_DAYS", "1,5,20")
    cost = float(os.getenv("EVALUATION_ESTIMATED_COST_PCT", "0.10"))
    days = [int(value.strip()) for value in raw.split(",") if value.strip()]
    if not days:
        raise ValueError("EVALUATION_HORIZONS_DAYS must contain at least one day")
    return [
        EvaluationHorizon(name=f"{day}d", after=timedelta(days=day), estimated_cost_pct=cost)
        for day in days
    ]


def build_worker_from_env() -> tuple[EvaluationWorker, Callable[[], None]]:
    from tradingagents.persistence import build_persistence_runtime

    runtime = build_persistence_runtime(
        {
            "persistence_backend": os.getenv("PERSISTENCE_BACKEND", "local"),
            "database_url": os.getenv("DATABASE_URL"),
        }
    )
    if runtime.unit_of_work_factory is None:
        runtime.close()
        raise ValueError("evaluation worker requires PERSISTENCE_BACKEND=postgres")
    provider_name = os.getenv("EVALUATION_PRICE_PROVIDER", "alpaca")
    prices = default_historical_price_registry().create(provider_name)
    return (
        EvaluationWorker(runtime.unit_of_work_factory, prices, _horizons_from_env()),
        runtime.close,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve trading evaluation outcomes")
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=float(os.getenv("EVALUATION_WORKER_INTERVAL_SECONDS", "300")),
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    worker, close = build_worker_from_env()
    try:
        if args.once:
            result = worker.run_once()
            LOGGER.info("evaluation cycle result=%s", result)
            return
        stop_event = threading.Event()
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_: stop_event.set())
        worker.run_forever(
            interval_seconds=args.interval_seconds,
            stop_event=stop_event,
        )
    finally:
        close()


if __name__ == "__main__":
    main()
