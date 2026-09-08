from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from tradingagents.agents.schemas import (
    ExecutableAction,
    RiskDecision,
    build_trade_intent_from_risk_decision,
)
from tradingagents.broker.models import (
    AccountSnapshot,
    PortfolioSnapshot,
    QuoteSnapshot,
)
from tradingagents.execution.dry_run_gateway import DryRunExecutionGateway
from tradingagents.execution.journal import ExecutionJournal
from tradingagents.execution.pipeline import ExecutionPipeline
from tradingagents.lifecycle import LifecycleRepository, LifecycleService
from tradingagents.persistence.postgres import (
    DatabaseSettings,
    PostgresUnitOfWork,
    create_database_engine,
    create_session_factory,
)
from tradingagents.safety import SafetyGuard


REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeProvider:
    def get_portfolio_snapshot(self):
        return PortfolioSnapshot(account=AccountSnapshot(equity=100_000.0))

    def get_quote_snapshot(self, symbol):
        return QuoteSnapshot(symbol=symbol, bid_price=99.0, ask_price=101.0)


class CountingGateway(DryRunExecutionGateway):
    def __init__(self):
        self.calls = 0
        self._lock = threading.Lock()

    def submit_plan(self, plan, intent):
        with self._lock:
            self.calls += 1
        time.sleep(0.1)
        return super().submit_plan(plan, intent)


def _intent():
    return build_trade_intent_from_risk_decision(
        symbol="AAPL",
        trading_mode="investment",
        current_position="NEUTRAL",
        decision=RiskDecision(
            action=ExecutableAction.BUY,
            confidence="high",
            risk_rationale="concurrency test",
            required_controls="test",
            target_portfolio_pct=5.0,
        ),
    )


def _guard(path: Path) -> SafetyGuard:
    return SafetyGuard(
        {"safety_enabled": False},
        state_path=path / "safety.json",
        kill_switch_path=path / "KILL_SWITCH",
    )


def _run_concurrently(pipelines, intent):
    barrier = threading.Barrier(2)

    def execute(pipeline):
        barrier.wait(timeout=5)
        return pipeline.execute("AAPL", intent, 1_000)

    with ThreadPoolExecutor(max_workers=2) as executor:
        return list(executor.map(execute, pipelines))


def test_local_lifecycle_allows_only_one_concurrent_execution(tmp_path: Path) -> None:
    gateway = CountingGateway()
    repository = LifecycleRepository(tmp_path / "lifecycle.sqlite3")
    pipeline = ExecutionPipeline(
        FakeProvider(),
        gateway,
        journal=ExecutionJournal(tmp_path),
        lifecycle=LifecycleService(repository),
        safety_guard=_guard(tmp_path),
    )

    results = _run_concurrently([pipeline, pipeline], _intent())

    assert gateway.calls == 1
    assert sum(bool(result.get("success")) for result in results) == 1
    assert sum(bool(result.get("duplicate")) for result in results) == 1


@pytest.fixture(scope="module")
def postgres_unit_of_work_factory():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_database_engine(DatabaseSettings(url=database_url))
    session_factory = create_session_factory(engine)
    try:
        yield lambda: PostgresUnitOfWork(session_factory)
    finally:
        engine.dispose()


def test_postgres_advisory_lock_allows_only_one_concurrent_execution(
    postgres_unit_of_work_factory, tmp_path: Path
) -> None:
    gateway = CountingGateway()
    intent = _intent()
    pipelines = [
        ExecutionPipeline(
            FakeProvider(),
            gateway,
            journal=ExecutionJournal(tmp_path),
            unit_of_work_factory=postgres_unit_of_work_factory,
            safety_guard=_guard(tmp_path),
        )
        for _ in range(2)
    ]

    results = _run_concurrently(pipelines, intent)

    assert gateway.calls == 1
    assert sum(bool(result.get("success")) for result in results) == 1
    assert sum(bool(result.get("duplicate")) for result in results) == 1
