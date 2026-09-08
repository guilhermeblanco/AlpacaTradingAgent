from datetime import datetime, timedelta, timezone

from tradingagents.evaluation import (
    EvaluationEpisode,
    EvaluationHorizon,
    EvaluationRepository,
    PriceObservation,
)
from tradingagents.evaluation.worker import EvaluationWorker


class Reservations:
    def __init__(self):
        self.expired = False

    def expire_due(self, now):
        if self.expired:
            return 0
        self.expired = True
        return 2


class UnitOfWork:
    active = 0

    def __init__(self, evaluation, reservations):
        self.evaluation = evaluation
        self.portfolio_reservations = reservations
        self.committed = False

    def __enter__(self):
        type(self).active += 1
        return self

    def __exit__(self, *args):
        type(self).active -= 1

    def commit(self):
        self.committed = True


class Prices:
    def __init__(self, now, fail_symbol=None):
        self.now = now
        self.fail_symbol = fail_symbol

    def price_at_or_after(self, symbol, at):
        assert UnitOfWork.active == 0
        if symbol == self.fail_symbol:
            raise LookupError("missing test price")
        return PriceObservation(symbol=symbol, price=110, observed_at=at)


def _episode(decision_id, symbol, now):
    return EvaluationEpisode(
        decision_id=decision_id,
        symbol=symbol,
        action="BUY",
        decision_at=now - timedelta(days=2),
        data_as_of=now - timedelta(days=2, minutes=1),
        reference_price=100,
        benchmark_symbol="SPY",
        benchmark_price=100,
    )


def test_worker_resolves_due_outcomes_outside_discovery_transaction(tmp_path) -> None:
    now = datetime(2026, 9, 8, 15, tzinfo=timezone.utc)
    repository = EvaluationRepository(tmp_path / "evaluation.sqlite3")
    repository.record_episode(_episode("decision-1", "AAPL", now))
    reservations = Reservations()
    factory = lambda: UnitOfWork(repository, reservations)
    worker = EvaluationWorker(
        factory,
        Prices(now),
        [EvaluationHorizon("1d", timedelta(days=1), 0.1)],
    )

    result = worker.run_once(now=now)

    assert result.pending == 1
    assert result.resolved == 1
    assert result.failed == 0
    assert result.expired_reservations == 2
    assert len(repository.outcomes()) == 1
    assert worker.run_once(now=now).pending == 0


def test_worker_isolates_price_failures_between_episodes(tmp_path) -> None:
    now = datetime(2026, 9, 8, 15, tzinfo=timezone.utc)
    repository = EvaluationRepository(tmp_path / "evaluation.sqlite3")
    repository.record_episode(_episode("bad", "MISSING", now))
    repository.record_episode(_episode("good", "AAPL", now))
    reservations = Reservations()
    worker = EvaluationWorker(
        lambda: UnitOfWork(repository, reservations),
        Prices(now, fail_symbol="MISSING"),
        [EvaluationHorizon("1d", timedelta(days=1))],
    )

    result = worker.run_once(now=now)

    assert result.pending == 2
    assert result.resolved == 1
    assert result.failed == 1
    assert result.errors[0].startswith("bad/1d:")
