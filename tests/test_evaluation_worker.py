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


class HeartbeatOperations:
    def __init__(self, error=None):
        self.beats = []
        self.error = error

    def beat(self, service, **kwargs):
        if self.error:
            raise self.error
        self.beats.append((service, kwargs))


class HeartbeatUnitOfWork(UnitOfWork):
    def __init__(self, evaluation, reservations, operations=None):
        super().__init__(evaluation, reservations)
        if operations is not None:
            self.operations = operations


def test_a_healthy_cycle_records_a_heartbeat(tmp_path) -> None:
    now = datetime(2026, 9, 8, 15, tzinfo=timezone.utc)
    repository = EvaluationRepository(tmp_path / "evaluation.sqlite3")
    operations = HeartbeatOperations()
    worker = EvaluationWorker(
        lambda: HeartbeatUnitOfWork(repository, Reservations(), operations),
        Prices(now),
        [EvaluationHorizon(name="1d", after=timedelta(days=1))],
        worker_id="evaluation-test",
    )

    worker.run_once(now=now)

    service, kwargs = operations.beats[0]
    assert service == "evaluation-worker"
    assert kwargs["instance_id"] == "evaluation-test"
    assert kwargs["status"] == "healthy"


def test_a_cycle_with_failures_reports_itself_degraded(tmp_path) -> None:
    """A supervisor reading the heartbeat has to see the difference."""
    now = datetime(2026, 9, 8, 15, tzinfo=timezone.utc)
    repository = EvaluationRepository(tmp_path / "evaluation.sqlite3")
    repository.record_episode(_episode("decision-1", "AAPL", now))
    operations = HeartbeatOperations()
    worker = EvaluationWorker(
        lambda: HeartbeatUnitOfWork(repository, Reservations(), operations),
        Prices(now, fail_symbol="AAPL"),
        [EvaluationHorizon(name="1d", after=timedelta(days=1))],
    )

    result = worker.run_once(now=now)

    assert result.failed == 1
    assert result.errors
    assert operations.beats[0][1]["status"] == "degraded"


def test_a_failing_heartbeat_does_not_lose_the_cycle_result(tmp_path) -> None:
    now = datetime(2026, 9, 8, 15, tzinfo=timezone.utc)
    repository = EvaluationRepository(tmp_path / "evaluation.sqlite3")
    worker = EvaluationWorker(
        lambda: HeartbeatUnitOfWork(
            repository, Reservations(), HeartbeatOperations(RuntimeError("db down"))
        ),
        Prices(now),
        [EvaluationHorizon(name="1d", after=timedelta(days=1))],
    )

    result = worker.run_once(now=now)

    assert result.pending == 0


def test_a_backend_without_operations_still_runs(tmp_path) -> None:
    now = datetime(2026, 9, 8, 15, tzinfo=timezone.utc)
    repository = EvaluationRepository(tmp_path / "evaluation.sqlite3")
    worker = EvaluationWorker(
        lambda: UnitOfWork(repository, Reservations()),
        Prices(now),
        [EvaluationHorizon(name="1d", after=timedelta(days=1))],
    )

    assert worker.run_once(now=now).pending == 0


def test_a_worker_id_is_stable_across_restarts() -> None:
    """It used to be a fresh uuid per process, so every restart left a
    heartbeat row nobody would ever update again — and the vitals strip,
    which counts rows, read four redeploys as "1/8 live"."""
    first = EvaluationWorker(lambda: None, None, []).worker_id
    second = EvaluationWorker(lambda: None, None, []).worker_id

    assert first == second


def test_replicas_are_still_told_apart(monkeypatch) -> None:
    """Stability must not become collision: two of the same worker do
    need separate rows, and that is what the replica id is for."""
    monkeypatch.setenv("REPLICA_ID", "2")
    scaled = EvaluationWorker(lambda: None, None, []).worker_id
    monkeypatch.delenv("REPLICA_ID")
    single = EvaluationWorker(lambda: None, None, []).worker_id

    assert scaled != single
    assert scaled.endswith("-2")


def test_an_explicit_id_always_wins() -> None:
    """Anyone running two in one process has to say so."""
    assert EvaluationWorker(lambda: None, None, [], worker_id="a").worker_id == "a"


def test_run_forever_stops_when_asked(tmp_path) -> None:
    import threading

    now = datetime(2026, 9, 8, 15, tzinfo=timezone.utc)
    repository = EvaluationRepository(tmp_path / "evaluation.sqlite3")
    stop_event = threading.Event()
    cycles = []

    worker = EvaluationWorker(
        lambda: UnitOfWork(repository, Reservations()),
        Prices(now),
        [EvaluationHorizon(name="1d", after=timedelta(days=1))],
    )
    original = worker.run_once

    def counting(**kwargs):
        cycles.append(1)
        stop_event.set()
        return original(**kwargs)

    worker.run_once = counting
    worker.run_forever(interval_seconds=0.01, stop_event=stop_event)

    assert cycles == [1]


def test_run_forever_will_not_spin(tmp_path) -> None:
    """A sub-second interval would hammer the database."""
    import threading

    repository = EvaluationRepository(tmp_path / "evaluation.sqlite3")
    stop_event = threading.Event()
    stop_event.set()

    worker = EvaluationWorker(
        lambda: UnitOfWork(repository, Reservations()), None, []
    )
    worker.run_forever(interval_seconds=0.0, stop_event=stop_event)


def test_horizons_come_from_the_environment(monkeypatch) -> None:
    from tradingagents.evaluation.worker import _horizons_from_env

    monkeypatch.setenv("EVALUATION_HORIZONS_DAYS", "1, 5,20")
    monkeypatch.setenv("EVALUATION_ESTIMATED_COST_PCT", "0.25")

    horizons = _horizons_from_env()

    assert [horizon.name for horizon in horizons] == ["1d", "5d", "20d"]
    assert horizons[0].after == timedelta(days=1)
    assert horizons[0].estimated_cost_pct == 0.25


def test_an_empty_horizon_list_is_refused(monkeypatch) -> None:
    import pytest

    from tradingagents.evaluation.worker import _horizons_from_env

    monkeypatch.setenv("EVALUATION_HORIZONS_DAYS", " , ")

    with pytest.raises(ValueError):
        _horizons_from_env()


def test_the_worker_requires_postgres(monkeypatch) -> None:
    import pytest

    from tradingagents.evaluation import worker as worker_module

    closed = []

    class Runtime:
        unit_of_work_factory = None

        def close(self):
            closed.append(True)

    monkeypatch.setattr(
        "tradingagents.persistence.build_persistence_runtime", lambda config: Runtime()
    )

    with pytest.raises(ValueError) as raised:
        worker_module.build_worker_from_env()

    assert "PERSISTENCE_BACKEND=postgres" in str(raised.value)
    assert closed == [True]


def test_the_worker_is_built_with_its_price_provider(monkeypatch) -> None:
    from types import SimpleNamespace

    from tradingagents.evaluation import worker as worker_module

    requested = {}

    class Runtime:
        unit_of_work_factory = staticmethod(lambda: None)

        def close(self):
            pass

    monkeypatch.setattr(
        "tradingagents.persistence.build_persistence_runtime", lambda config: Runtime()
    )
    monkeypatch.setattr(
        worker_module,
        "default_historical_price_registry",
        lambda: SimpleNamespace(
            create=lambda name: requested.setdefault("provider", name)
        ),
    )
    monkeypatch.setenv("EVALUATION_PRICE_PROVIDER", "tradier")
    monkeypatch.setenv("EVALUATION_HORIZONS_DAYS", "1")

    worker, close = worker_module.build_worker_from_env()

    assert requested["provider"] == "tradier"
    assert [horizon.name for horizon in worker.horizons] == ["1d"]
    assert callable(close)


def test_the_cli_runs_one_cycle_and_releases_the_engine(monkeypatch) -> None:
    from unittest import mock

    from tradingagents.evaluation import worker as worker_module

    worker = mock.Mock()
    worker.run_once.return_value = "result"
    closed = []

    monkeypatch.setattr(
        worker_module,
        "build_worker_from_env",
        lambda: (worker, lambda: closed.append(True)),
    )
    monkeypatch.setattr("sys.argv", ["evaluation-worker", "--once"])

    worker_module.main()

    worker.run_once.assert_called_once()
    worker.run_forever.assert_not_called()
    assert closed == [True]


def test_the_cli_loops_on_the_configured_interval(monkeypatch) -> None:
    from unittest import mock

    from tradingagents.evaluation import worker as worker_module

    worker = mock.Mock()
    monkeypatch.setattr(
        worker_module, "build_worker_from_env", lambda: (worker, lambda: None)
    )
    monkeypatch.setattr("sys.argv", ["evaluation-worker", "--interval-seconds", "12"])

    worker_module.main()

    assert worker.run_forever.call_args.kwargs["interval_seconds"] == 12.0


def test_the_engine_is_released_when_a_cycle_raises(monkeypatch) -> None:
    import pytest
    from unittest import mock

    from tradingagents.evaluation import worker as worker_module

    worker = mock.Mock()
    worker.run_once.side_effect = RuntimeError("database gone")
    closed = []

    monkeypatch.setattr(
        worker_module,
        "build_worker_from_env",
        lambda: (worker, lambda: closed.append(True)),
    )
    monkeypatch.setattr("sys.argv", ["evaluation-worker", "--once"])

    with pytest.raises(RuntimeError):
        worker_module.main()

    assert closed == [True]
