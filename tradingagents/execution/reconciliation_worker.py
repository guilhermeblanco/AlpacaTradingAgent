"""Lease-based worker that reconciles accepted broker orders."""

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

from .models import ExecutionResult
from .reconciliation import BrokerOrderStatus, PersistentExecutionReconciler


LOGGER = logging.getLogger(__name__)
TERMINAL = {
    BrokerOrderStatus.FILLED,
    BrokerOrderStatus.CANCELED,
    BrokerOrderStatus.REJECTED,
    BrokerOrderStatus.EXPIRED,
}


@dataclass
class ReconciliationWorkerResult:
    claimed: int = 0
    completed: int = 0
    rescheduled: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


class ReconciliationWorker:
    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        gateway_factory: Callable[[str], Any],
        *,
        evaluation_prices=None,
        evaluation_benchmark_symbol: str = "SPY",
        evaluation_experiment_id: str = "default",
        worker_id: Optional[str] = None,
        batch_size: int = 25,
        lease_seconds: int = 60,
        poll_seconds: float = 5.0,
        max_retry_seconds: float = 300.0,
    ):
        self.unit_of_work_factory = unit_of_work_factory
        self.gateway_factory = gateway_factory
        self.evaluation_prices = evaluation_prices
        self.evaluation_benchmark_symbol = evaluation_benchmark_symbol
        self.evaluation_experiment_id = evaluation_experiment_id
        self.worker_id = worker_id or f"reconciler-{uuid4()}"
        self.batch_size = max(1, int(batch_size))
        self.lease_seconds = max(1, int(lease_seconds))
        self.poll_seconds = max(1.0, float(poll_seconds))
        self.max_retry_seconds = max(self.poll_seconds, float(max_retry_seconds))

    def run_once(self, *, now: Optional[datetime] = None) -> ReconciliationWorkerResult:
        now = now or datetime.now(timezone.utc)
        with self.unit_of_work_factory() as uow:
            tasks = uow.reconciliation_queue.claim(
                worker_id=self.worker_id,
                limit=self.batch_size,
                lease_seconds=self.lease_seconds,
                now=now,
            )
            uow.commit()
        result = ReconciliationWorkerResult(claimed=len(tasks))
        for task in tasks:
            try:
                execution = ExecutionResult.model_validate(task.execution_result)
                gateway = self.gateway_factory(task.broker)
                report = PersistentExecutionReconciler(
                    gateway,
                    self.unit_of_work_factory,
                    evaluation_prices=self.evaluation_prices,
                    evaluation_benchmark_symbol=self.evaluation_benchmark_symbol,
                    evaluation_experiment_id=self.evaluation_experiment_id,
                ).reconcile(execution.plan, execution.actions)
                terminal = bool(report.legs) and all(
                    leg.status in TERMINAL for leg in report.legs
                )
                with self.unit_of_work_factory() as uow:
                    if terminal:
                        uow.reconciliation_queue.complete(
                            task.decision_id,
                            worker_id=self.worker_id,
                            now=now,
                        )
                        uow.portfolio_reservations.release_decision(
                            task.decision_id, now=now
                        )
                        result.completed += 1
                    else:
                        uow.reconciliation_queue.retry(
                            task.decision_id,
                            worker_id=self.worker_id,
                            retry_at=now + timedelta(seconds=self.poll_seconds),
                        )
                        result.rescheduled += 1
                    uow.commit()
            except Exception as exc:
                result.failed += 1
                message = f"{task.decision_id}: {exc}"
                result.errors.append(message)
                delay = min(
                    self.max_retry_seconds,
                    self.poll_seconds * (2 ** max(0, task.attempts - 1)),
                )
                try:
                    with self.unit_of_work_factory() as uow:
                        uow.reconciliation_queue.retry(
                            task.decision_id,
                            worker_id=self.worker_id,
                            retry_at=now + timedelta(seconds=delay),
                            error=str(exc),
                        )
                        uow.commit()
                except Exception:
                    LOGGER.exception("Failed to release reconciliation lease")
                LOGGER.exception("Reconciliation failed for %s", message)
        try:
            with self.unit_of_work_factory() as uow:
                operations = getattr(uow, "operations", None)
                if operations is not None:
                    operations.beat(
                        "reconciliation-worker",
                        instance_id=self.worker_id,
                        status="degraded" if result.failed else "healthy",
                        details={
                            "claimed": result.claimed,
                            "completed": result.completed,
                            "rescheduled": result.rescheduled,
                            "failed": result.failed,
                        },
                        now=now,
                    )
                uow.commit()
        except Exception:
            LOGGER.exception("Failed to record reconciliation heartbeat")
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
                "reconciliation cycle claimed=%s completed=%s rescheduled=%s failed=%s",
                result.claimed,
                result.completed,
                result.rescheduled,
                result.failed,
            )
            stop_event.wait(interval_seconds)


def build_worker_from_env() -> tuple[ReconciliationWorker, Callable[[], None]]:
    from tradingagents.broker.registry import default_broker_registry
    from tradingagents.evaluation import default_historical_price_registry
    from tradingagents.persistence import build_persistence_runtime

    runtime = build_persistence_runtime(
        {
            "persistence_backend": os.getenv("PERSISTENCE_BACKEND", "local"),
            "database_url": os.getenv("DATABASE_URL"),
        }
    )
    if runtime.unit_of_work_factory is None:
        runtime.close()
        raise ValueError("reconciliation worker requires PERSISTENCE_BACKEND=postgres")
    brokers = default_broker_registry()

    def gateway_factory(recorded_broker: str):
        broker = "alpaca" if recorded_broker.startswith("alpaca") else recorded_broker
        return brokers.create(broker).execution_gateway

    provider_name = os.getenv("EVALUATION_PRICE_PROVIDER", "alpaca")
    prices = default_historical_price_registry().create(provider_name)
    worker = ReconciliationWorker(
        runtime.unit_of_work_factory,
        gateway_factory,
        evaluation_prices=prices,
        evaluation_benchmark_symbol=os.getenv("EVALUATION_BENCHMARK_SYMBOL", "SPY"),
        evaluation_experiment_id=os.getenv("EVALUATION_EXPERIMENT_ID", "default"),
        batch_size=int(os.getenv("RECONCILIATION_WORKER_BATCH_SIZE", "25")),
        lease_seconds=int(os.getenv("RECONCILIATION_LEASE_SECONDS", "60")),
        poll_seconds=float(os.getenv("RECONCILIATION_POLL_SECONDS", "5")),
        max_retry_seconds=float(os.getenv("RECONCILIATION_MAX_RETRY_SECONDS", "300")),
    )
    return worker, runtime.close


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile accepted broker orders")
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=float(os.getenv("RECONCILIATION_WORKER_INTERVAL_SECONDS", "5")),
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    worker, close = build_worker_from_env()
    try:
        if args.once:
            LOGGER.info("reconciliation cycle result=%s", worker.run_once())
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
