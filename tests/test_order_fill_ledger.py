from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select

from tradingagents.agents.schemas import (
    ExecutableAction,
    RiskDecision,
    build_trade_intent_from_risk_decision,
)
from tradingagents.broker.models import AccountSnapshot, PortfolioSnapshot, QuoteSnapshot
from tradingagents.execution.journal import ExecutionJournal
from tradingagents.execution.gateway import SubmissionUncertain
from tradingagents.execution.pipeline import ExecutionPipeline

from tradingagents.execution.models import (
    ExecutionLeg,
    ExecutionPlan,
    ExecutionResult,
    PlanAction,
)
from tradingagents.execution.reconciliation import (
    BrokerOrderSnapshot,
    BrokerOrderStatus,
    PersistentExecutionReconciler,
)
from tradingagents.execution.reconciliation_worker import ReconciliationWorker
from tradingagents.lifecycle import LifecycleStatus
from tradingagents.persistence.postgres import (
    Base,
    PostgresUnitOfWork,
    create_session_factory,
)
from tradingagents.persistence.postgres.models import (
    BrokerFillRow,
    BrokerOrderRow,
    LifecycleRow,
)
from tradingagents.portfolio import (
    AllocationReservationState,
    BatchAllocationStatus,
    PortfolioAllocation,
    PortfolioDecisionBatch,
)
from tradingagents.safety import SafetyGuard


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


class UncertainGateway:
    name = "alpaca-paper"

    def __init__(self):
        self.calls = 0

    def submit_plan(self, plan, intent):
        self.calls += 1
        client_order_id = plan.metadata["leg_idempotency_keys"][0]
        raise SubmissionUncertain(
            "broker response timed out",
            gateway=self.name,
            leg_index=0,
            actions=[
                {
                    "action": "buy",
                    "leg": plan.legs[0].model_dump(mode="json"),
                    "result": {
                        "success": False,
                        "status": "unknown",
                        "client_order_id": client_order_id,
                        "submission_uncertain": True,
                    },
                }
            ],
        )


class PipelineProvider:
    def get_portfolio_snapshot(self):
        return PortfolioSnapshot(account=AccountSnapshot(equity=100_000))

    def get_quote_snapshot(self, symbol):
        return QuoteSnapshot(symbol=symbol, bid_price=99, ask_price=101)


def test_uncertain_pipeline_result_is_durable_and_not_resubmitted(
    session_factory, tmp_path
) -> None:
    intent = build_trade_intent_from_risk_decision(
        symbol="AAPL",
        trading_mode="investment",
        current_position="NEUTRAL",
        decision=RiskDecision(
            action=ExecutableAction.BUY,
            confidence="high",
            risk_rationale="uncertainty test",
            required_controls="test",
            target_portfolio_pct=1,
        ),
    )
    gateway = UncertainGateway()
    pipeline = ExecutionPipeline(
        PipelineProvider(),
        gateway,
        journal=ExecutionJournal(tmp_path),
        safety_guard=SafetyGuard(
            {"safety_enabled": False},
            state_path=tmp_path / "safety.json",
            kill_switch_path=tmp_path / "KILL_SWITCH",
        ),
        unit_of_work_factory=lambda: PostgresUnitOfWork(session_factory),
    )

    first = pipeline.execute("AAPL", intent, 1_000)
    second = pipeline.execute("AAPL", intent, 1_000)

    assert not first["success"]
    assert first["submission_uncertain"]
    assert second == first
    assert gateway.calls == 1
    with PostgresUnitOfWork(session_factory) as uow:
        lifecycle = uow.lifecycle.get(intent.decision_id)
        orders = uow.orders.orders_for_decision(intent.decision_id)
        tasks = uow.reconciliation_queue.claim(worker_id="test")
        uow.rollback()
    assert lifecycle.status is LifecycleStatus.SUBMITTED
    assert len(orders) == 1
    assert orders[0].broker_order_id is None
    assert orders[0].status == "unknown"
    assert tasks[0].execution_result["submission_uncertain"]


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        decision_id="decision-order-ledger",
        symbol="AAPL",
        intent_schema_version="1.0",
        current_allocation_pct=0,
        target_allocation_pct=1,
        current_notional_usd=0,
        target_notional_usd=1000,
        delta_notional_usd=1000,
        reference_price=100,
        legs=[
            ExecutionLeg(
                action=PlanAction.BUY,
                side="buy",
                notional_usd=1000,
                quantity=10,
                reason="test",
            )
        ],
        metadata={"leg_idempotency_keys": ["client-order-1"]},
    )


def _seed_submission(session_factory, gateway="alpaca-paper") -> ExecutionPlan:
    plan = _plan()
    result = ExecutionResult(
        success=True,
        decision_id=plan.decision_id,
        symbol=plan.symbol,
        gateway=gateway,
        plan=plan,
        actions=[{"result": {"success": True, "order_id": "broker-order-1", "status": "new"}}],
    )
    with PostgresUnitOfWork(session_factory) as uow:
        uow.lifecycle.create(
            decision_id=plan.decision_id,
            symbol=plan.symbol,
            idempotency_key="decision-key",
        )
        uow.orders.record_submission(result)
        uow.lifecycle.transition(
            plan.decision_id,
            LifecycleStatus.SUBMITTED,
            result=result.model_dump(mode="json"),
        )
        uow.commit()
    return plan


def test_order_ledger_derives_incremental_fills_from_cumulative_snapshots(
    session_factory,
) -> None:
    plan = _seed_submission(session_factory)
    now = datetime.now(timezone.utc)
    with PostgresUnitOfWork(session_factory) as uow:
        partial = uow.orders.apply_snapshot(
            decision_id=plan.decision_id,
            leg_index=0,
            snapshot=BrokerOrderSnapshot(
                order_id="broker-order-1",
                client_order_id="client-order-1",
                symbol="AAPL",
                side="buy",
                status=BrokerOrderStatus.PARTIALLY_FILLED,
                requested_quantity=10,
                filled_quantity=4,
                filled_avg_price=100,
            ),
            observed_at=now,
        )
        filled = uow.orders.apply_snapshot(
            decision_id=plan.decision_id,
            leg_index=0,
            snapshot=BrokerOrderSnapshot(
                order_id="broker-order-1",
                client_order_id="client-order-1",
                symbol="AAPL",
                side="buy",
                status=BrokerOrderStatus.FILLED,
                requested_quantity=10,
                filled_quantity=10,
                filled_avg_price=102,
            ),
            observed_at=now,
        )
        uow.commit()

    assert partial.filled_quantity == 4
    assert filled.status == BrokerOrderStatus.FILLED.value
    with session_factory() as session:
        fills = session.scalars(
            select(BrokerFillRow).order_by(BrokerFillRow.fill_sequence)
        ).all()
        assert [fill.quantity for fill in fills] == [4, 6]
        assert fills[0].price == pytest.approx(100)
        assert fills[1].price == pytest.approx(103.3333333)


def test_reconciliation_queue_uses_exclusive_durable_leases(session_factory) -> None:
    plan = _seed_submission(session_factory)
    now = datetime.now(timezone.utc)
    with PostgresUnitOfWork(session_factory) as uow:
        first = uow.reconciliation_queue.claim(
            worker_id="worker-a", now=now, lease_seconds=30
        )
        uow.commit()
    with PostgresUnitOfWork(session_factory) as uow:
        blocked = uow.reconciliation_queue.claim(worker_id="worker-b", now=now)
        uow.rollback()

    assert [task.decision_id for task in first] == [plan.decision_id]
    assert first[0].execution_result["decision_id"] == plan.decision_id
    assert blocked == []

    with PostgresUnitOfWork(session_factory) as uow:
        uow.reconciliation_queue.retry(
            plan.decision_id,
            worker_id="worker-a",
            retry_at=now + timedelta(seconds=10),
            error="still open",
        )
        uow.commit()
    with PostgresUnitOfWork(session_factory) as uow:
        assert uow.reconciliation_queue.claim(
            worker_id="worker-b", now=now + timedelta(seconds=9)
        ) == []
        due = uow.reconciliation_queue.claim(
            worker_id="worker-b", now=now + timedelta(seconds=10)
        )
        uow.commit()
    assert len(due) == 1


def test_reconciliation_worker_completes_filled_order(session_factory) -> None:
    plan = _seed_submission(session_factory, gateway="tradier")
    with PostgresUnitOfWork(session_factory) as uow:
        reservation = uow.portfolio_reservations.reserve(
            PortfolioDecisionBatch(
                batch_id="order-ledger-batch",
                created_at=datetime.now(timezone.utc),
                snapshot_hash="snapshot",
                snapshot_captured_at="2026-09-08T15:00:00+00:00",
                account_equity_usd=100_000,
                starting_gross_exposure_usd=0,
                starting_symbol_exposure_usd={},
                gross_limit_usd=100_000,
                max_symbol_concentration_pct=100,
                ending_reserved_exposure_usd=1_000,
                allocations=[
                    PortfolioAllocation(
                        decision_id=plan.decision_id,
                        symbol=plan.symbol,
                        status=BatchAllocationStatus.APPROVED,
                        requested_notional_usd=1_000,
                        approved_notional_usd=1_000,
                        priority=1,
                    )
                ],
            ),
            account_key="tradier:test",
        )
        uow.portfolio_reservations.consume(
            reservation.reservation_id, decision_id=plan.decision_id
        )
        uow.commit()

    class FilledGateway:
        def get_order_snapshot(self, **kwargs):
            return BrokerOrderSnapshot(
                order_id="broker-order-1",
                symbol="AAPL",
                side="buy",
                status=BrokerOrderStatus.FILLED,
                requested_quantity=10,
                filled_quantity=10,
                filled_avg_price=101,
            )

    class Prices:
        def price_at_or_before(self, symbol, at):
            from tradingagents.evaluation import PriceObservation

            return PriceObservation(
                symbol=symbol,
                price=500,
                observed_at=at - timedelta(minutes=1),
            )

    brokers = []

    def gateway_factory(broker):
        brokers.append(broker)
        return FilledGateway()

    result = ReconciliationWorker(
        lambda: PostgresUnitOfWork(session_factory),
        gateway_factory,
        evaluation_prices=Prices(),
        worker_id="test-worker",
    ).run_once()

    assert result.claimed == 1
    assert result.completed == 1
    assert result.failed == 0
    assert brokers == ["tradier"]
    with PostgresUnitOfWork(session_factory) as uow:
        assert uow.lifecycle.get(plan.decision_id).status is LifecycleStatus.FILLED
        assert uow.reconciliation_queue.claim(worker_id="other") == []
        recorded = uow.portfolio_reservations.reserve(
            reservation.batch, account_key="tradier:test"
        )
        assert recorded.allocation_states[plan.decision_id] is AllocationReservationState.RELEASED
        uow.rollback()


def test_persistent_reconciler_updates_order_lifecycle_and_event(session_factory) -> None:
    plan = _seed_submission(session_factory)

    class FilledGateway:
        def get_order_snapshot(self, **kwargs):
            return BrokerOrderSnapshot(
                order_id="broker-order-1",
                symbol="AAPL",
                side="buy",
                status=BrokerOrderStatus.FILLED,
                requested_quantity=10,
                filled_quantity=10,
                filled_avg_price=101,
            )

    report = PersistentExecutionReconciler(
        FilledGateway(), lambda: PostgresUnitOfWork(session_factory)
    ).reconcile(plan, [{"result": {"order_id": "broker-order-1"}}])

    assert report.complete
    with session_factory() as session:
        assert session.get(LifecycleRow, plan.decision_id).status == "filled"
        assert session.scalar(select(func.count()).select_from(BrokerFillRow)) == 1


@pytest.mark.parametrize("broker", ["alpaca", "tradier", "robinhood"])
def test_reconciliation_captures_fill_episode_for_every_broker(
    session_factory, broker
) -> None:
    plan = _seed_submission(session_factory, gateway=broker)
    plan.metadata["experiment_id"] = "assigned-champion"

    class FilledGateway:
        def get_order_snapshot(self, **kwargs):
            return BrokerOrderSnapshot(
                order_id="broker-order-1",
                symbol="AAPL",
                side="buy",
                status=BrokerOrderStatus.FILLED,
                requested_quantity=10,
                filled_quantity=10,
                filled_avg_price=101,
            )

    class Prices:
        def price_at_or_before(self, symbol, at):
            from tradingagents.evaluation import PriceObservation

            return PriceObservation(
                symbol=symbol,
                price=500,
                observed_at=at - timedelta(minutes=1),
            )

    report = PersistentExecutionReconciler(
        FilledGateway(),
        lambda: PostgresUnitOfWork(session_factory),
        evaluation_prices=Prices(),
    ).reconcile(plan, [{"result": {"order_id": "broker-order-1"}}])

    assert report.complete
    with PostgresUnitOfWork(session_factory) as uow:
        episode = uow.evaluation.get_episode(plan.decision_id)
        uow.rollback()
    assert episode is not None
    assert episode.reference_price == 101
    assert episode.experiment_id == "assigned-champion"
    assert episode.metadata["entry_time_source"] == "reconciliation_observed_at"


def test_evaluation_price_failure_does_not_block_reconciliation(
    session_factory,
) -> None:
    plan = _seed_submission(session_factory)

    class FilledGateway:
        def get_order_snapshot(self, **kwargs):
            return BrokerOrderSnapshot(
                order_id="broker-order-1",
                symbol="AAPL",
                side="buy",
                status=BrokerOrderStatus.FILLED,
                filled_quantity=10,
                filled_avg_price=101,
            )

    class MissingPrices:
        def price_at_or_before(self, symbol, at):
            raise LookupError("benchmark unavailable")

    report = PersistentExecutionReconciler(
        FilledGateway(),
        lambda: PostgresUnitOfWork(session_factory),
        evaluation_prices=MissingPrices(),
    ).reconcile(plan, [{"result": {"order_id": "broker-order-1"}}])

    assert report.complete
    with PostgresUnitOfWork(session_factory) as uow:
        assert uow.lifecycle.get(plan.decision_id).status is LifecycleStatus.FILLED
        assert uow.evaluation.get_episode(plan.decision_id) is None
        uow.rollback()


def test_risk_reducing_fill_does_not_create_episode(session_factory) -> None:
    plan = _seed_submission(session_factory)
    plan.legs[0].risk_reducing = True
    plan.legs[0].action = PlanAction.CLOSE
    plan.legs[0].side = "sell"

    class FilledGateway:
        def get_order_snapshot(self, **kwargs):
            return BrokerOrderSnapshot(
                order_id="broker-order-1",
                symbol="AAPL",
                side="sell",
                status=BrokerOrderStatus.FILLED,
                filled_quantity=10,
                filled_avg_price=101,
            )

    class Prices:
        def price_at_or_before(self, symbol, at):
            raise AssertionError("risk-reducing fills must not request evaluation prices")

    PersistentExecutionReconciler(
        FilledGateway(),
        lambda: PostgresUnitOfWork(session_factory),
        evaluation_prices=Prices(),
    ).reconcile(plan, [{"result": {"order_id": "broker-order-1"}}])

    with PostgresUnitOfWork(session_factory) as uow:
        assert uow.evaluation.get_episode(plan.decision_id) is None
        uow.rollback()


def test_pipeline_persists_remote_acceptance_as_submitted(
    session_factory, tmp_path
) -> None:
    intent = build_trade_intent_from_risk_decision(
        symbol="AAPL",
        trading_mode="investment",
        current_position="NEUTRAL",
        decision=RiskDecision(
            action=ExecutableAction.BUY,
            confidence="high",
            risk_rationale="test",
            required_controls="test",
            target_portfolio_pct=1,
        ),
    )

    class Provider:
        def get_portfolio_snapshot(self):
            return PortfolioSnapshot(account=AccountSnapshot(equity=100_000))

        def get_quote_snapshot(self, symbol):
            return QuoteSnapshot(symbol=symbol, bid_price=99, ask_price=101)

    class Gateway:
        name = "test-paper"

        def submit_plan(self, plan, submitted_intent):
            return ExecutionResult(
                success=True,
                decision_id=plan.decision_id,
                symbol=plan.symbol,
                gateway=self.name,
                plan=plan,
                actions=[
                    {"result": {"success": True, "order_id": "remote-1", "status": "accepted"}}
                ],
            )

    result = ExecutionPipeline(
        Provider(),
        Gateway(),
        journal=ExecutionJournal(tmp_path),
        unit_of_work_factory=lambda: PostgresUnitOfWork(session_factory),
        safety_guard=SafetyGuard(
            {"safety_enabled": False},
            state_path=tmp_path / "safety.json",
            kill_switch_path=tmp_path / "KILL_SWITCH",
        ),
    ).execute("AAPL", intent, 1_000)

    assert result["success"]
    with session_factory() as session:
        assert session.get(LifecycleRow, intent.decision_id).status == "submitted"
        assert session.scalar(select(func.count()).select_from(BrokerOrderRow)) == 1
