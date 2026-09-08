"""Session-bound PostgreSQL implementations of persistence ports."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tradingagents.evaluation.models import EvaluationEpisode, EvaluationOutcome
from tradingagents.evaluation.point_in_time import validate_episode_point_in_time
from tradingagents.lifecycle.models import LifecycleRecord, LifecycleStatus
from tradingagents.operations.admission import AdmissionDecision
from tradingagents.persistence.events import EventEnvelope
from tradingagents.execution.models import ExecutionResult, PlanAction
from tradingagents.execution.order_ledger import BrokerOrderRecord
from tradingagents.execution.reconciliation import BrokerOrderSnapshot, BrokerOrderStatus
from tradingagents.execution.reconciliation_queue import (
    ReconciliationLeaseLost,
    ReconciliationTask,
)
from tradingagents.portfolio.batch import BatchAllocationStatus, PortfolioDecisionBatch
from tradingagents.portfolio.reservations import (
    AllocationReservationState,
    PortfolioReservation,
    ReservationStatus,
)

from .models import (
    AnalysisAdmissionRow,
    BrokerFillRow,
    BrokerOrderRow,
    BrokerOrderTransitionRow,
    DecisionEventRow,
    EvaluationEpisodeRow,
    EvaluationOutcomeRow,
    LifecycleRow,
    LifecycleTransitionRow,
    OutboxRow,
    PortfolioReservationAllocationRow,
    PortfolioReservationRow,
    PortfolioReservationTransitionRow,
    ReconciliationLeaseRow,
)
from tradingagents.persistence.outbox import OutboxLeaseLost, OutboxMessage


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _symbol_key(symbol: str) -> str:
    return (symbol or "").upper().replace("/", "").strip()


class PostgresEventJournal:
    def __init__(self, session: Session):
        self.session = session

    def append(
        self,
        event_type: str,
        *,
        symbol: str,
        decision_id: str,
        run_id: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> str:
        current_version = self.session.scalar(
            select(func.max(DecisionEventRow.aggregate_version)).where(
                DecisionEventRow.aggregate_type == "decision",
                DecisionEventRow.aggregate_id == decision_id,
            )
        ) or 0
        envelope = EventEnvelope(
            event_type=event_type,
            aggregate_type="decision",
            aggregate_id=decision_id,
            aggregate_version=current_version + 1,
            correlation_id=run_id or decision_id,
            payload={"symbol": symbol, **(payload or {})},
        )
        self.session.add(
            DecisionEventRow(
                event_id=envelope.event_id,
                event_type=envelope.event_type,
                aggregate_type=envelope.aggregate_type,
                aggregate_id=envelope.aggregate_id,
                aggregate_version=envelope.aggregate_version,
                occurred_at=envelope.occurred_at,
                recorded_at=envelope.recorded_at,
                correlation_id=envelope.correlation_id,
                causation_id=envelope.causation_id,
                idempotency_key=envelope.idempotency_key,
                schema_version=envelope.schema_version,
                payload=envelope.payload,
                payload_sha256=envelope.payload_sha256,
            )
        )
        self.session.flush()
        return envelope.event_id


class PostgresLifecycleRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, decision_id: str) -> Optional[LifecycleRecord]:
        row = self.session.get(LifecycleRow, decision_id)
        return self._record(row) if row is not None else None

    def create(
        self,
        *,
        decision_id: str,
        symbol: str,
        idempotency_key: str,
        valid_until: Optional[datetime] = None,
        run_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> LifecycleRecord:
        record, _ = self.create_once(
            decision_id=decision_id,
            symbol=symbol,
            idempotency_key=idempotency_key,
            valid_until=valid_until,
            run_id=run_id,
            metadata=metadata,
        )
        return record

    def create_once(
        self,
        *,
        decision_id: str,
        symbol: str,
        idempotency_key: str,
        valid_until: Optional[datetime] = None,
        run_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> tuple[LifecycleRecord, bool]:
        if self.session.bind is not None and self.session.bind.dialect.name == "postgresql":
            self.session.execute(
                select(
                    func.pg_advisory_xact_lock(
                        func.hashtextextended(decision_id, 0)
                    )
                )
            )
        existing = self.session.get(LifecycleRow, decision_id)
        if existing is not None:
            return self._record(existing), False
        now = _utcnow()
        row = LifecycleRow(
            decision_id=decision_id,
            symbol=symbol,
            status=LifecycleStatus.RECEIVED.value,
            idempotency_key=idempotency_key,
            created_at=now,
            updated_at=now,
            valid_until=valid_until,
            run_id=run_id,
            metadata_payload=metadata or {},
        )
        self.session.add(row)
        self.session.add(
            LifecycleTransitionRow(
                decision_id=decision_id,
                recorded_at=now,
                from_status=None,
                to_status=LifecycleStatus.RECEIVED.value,
                payload={},
            )
        )
        self.session.flush()
        return self._record(row), True

    def transition(
        self,
        decision_id: str,
        status: LifecycleStatus,
        *,
        error: Optional[str] = None,
        result: Optional[dict[str, Any]] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> LifecycleRecord:
        row = self.session.scalar(
            select(LifecycleRow)
            .where(LifecycleRow.decision_id == decision_id)
            .with_for_update()
        )
        if row is None:
            raise KeyError(f"Unknown lifecycle decision {decision_id}")
        now = _utcnow()
        previous = row.status
        row.status = status.value
        row.updated_at = now
        row.error = error
        if result is not None:
            row.result = result
        self.session.add(
            LifecycleTransitionRow(
                decision_id=decision_id,
                recorded_at=now,
                from_status=previous,
                to_status=status.value,
                payload=payload or {},
            )
        )
        self.session.flush()
        return self._record(row)

    def expire_due(self, now: Optional[datetime] = None) -> int:
        now = now or _utcnow()
        terminal = {
            LifecycleStatus.SUCCEEDED.value,
            LifecycleStatus.FILLED.value,
            LifecycleStatus.BLOCKED.value,
            LifecycleStatus.FAILED.value,
            LifecycleStatus.EXPIRED.value,
            LifecycleStatus.CANCELLED.value,
        }
        rows = self.session.scalars(
            select(LifecycleRow)
            .where(
                LifecycleRow.valid_until.is_not(None),
                LifecycleRow.valid_until < now,
                LifecycleRow.status.not_in(terminal),
            )
            .with_for_update()
        ).all()
        for row in rows:
            self.transition(
                row.decision_id,
                LifecycleStatus.EXPIRED,
                error="Intent validity window elapsed",
            )
        return len(rows)

    @staticmethod
    def _record(row: LifecycleRow) -> LifecycleRecord:
        return LifecycleRecord(
            decision_id=row.decision_id,
            symbol=row.symbol,
            status=LifecycleStatus(row.status),
            idempotency_key=row.idempotency_key,
            created_at=row.created_at,
            updated_at=row.updated_at,
            valid_until=row.valid_until,
            run_id=row.run_id,
            error=row.error,
            result=row.result,
            metadata=row.metadata_payload or {},
        )


class PostgresEvaluationRepository:
    def __init__(self, session: Session):
        self.session = session

    def record_episode(self, episode: EvaluationEpisode) -> None:
        validate_episode_point_in_time(episode)
        if self.session.get(EvaluationEpisodeRow, episode.decision_id) is not None:
            return
        try:
            with self.session.begin_nested():
                self.session.add(
                    EvaluationEpisodeRow(
                        decision_id=episode.decision_id,
                        symbol=episode.symbol,
                        action=episode.action,
                        decision_at=episode.decision_at,
                        data_as_of=episode.data_as_of,
                        reference_price=episode.reference_price,
                        benchmark_symbol=episode.benchmark_symbol,
                        benchmark_price=episode.benchmark_price,
                        confidence=episode.confidence,
                        experiment_id=episode.experiment_id,
                        metadata_payload=episode.metadata,
                    )
                )
                self.session.flush()
        except IntegrityError:
            if self.session.get(EvaluationEpisodeRow, episode.decision_id) is None:
                raise

    def get_episode(self, decision_id: str) -> Optional[EvaluationEpisode]:
        row = self.session.get(EvaluationEpisodeRow, decision_id)
        if row is None:
            return None
        return self._episode(row)

    def record_outcome(self, outcome: EvaluationOutcome) -> None:
        if self.get_episode(outcome.decision_id) is None:
            raise KeyError(f"Unknown evaluation episode {outcome.decision_id}")
        row = self.session.scalar(
            select(EvaluationOutcomeRow).where(
                EvaluationOutcomeRow.decision_id == outcome.decision_id,
                EvaluationOutcomeRow.horizon == outcome.horizon,
            )
        )
        if row is not None:
            return
        try:
            with self.session.begin_nested():
                self.session.add(EvaluationOutcomeRow(**outcome.model_dump()))
                self.session.flush()
        except IntegrityError:
            exists = self.session.scalar(
                select(EvaluationOutcomeRow.outcome_id).where(
                    EvaluationOutcomeRow.decision_id == outcome.decision_id,
                    EvaluationOutcomeRow.horizon == outcome.horizon,
                )
            )
            if exists is None:
                raise

    def pending_episodes(
        self, *, horizon: str, due_before: datetime
    ) -> list[EvaluationEpisode]:
        outcome_exists = (
            select(EvaluationOutcomeRow.outcome_id)
            .where(
                EvaluationOutcomeRow.decision_id == EvaluationEpisodeRow.decision_id,
                EvaluationOutcomeRow.horizon == horizon,
            )
            .exists()
        )
        rows = self.session.scalars(
            select(EvaluationEpisodeRow)
            .where(
                EvaluationEpisodeRow.decision_at <= due_before,
                ~outcome_exists,
            )
            .order_by(EvaluationEpisodeRow.decision_at, EvaluationEpisodeRow.decision_id)
        ).all()
        return [self._episode(row) for row in rows]

    def outcomes(
        self, *, experiment_id: Optional[str] = None
    ) -> list[EvaluationOutcome]:
        statement = select(EvaluationOutcomeRow).join(EvaluationEpisodeRow)
        if experiment_id:
            statement = statement.where(
                EvaluationEpisodeRow.experiment_id == experiment_id
            )
        statement = statement.order_by(EvaluationOutcomeRow.outcome_at)
        return [
            EvaluationOutcome(
                decision_id=row.decision_id,
                horizon=row.horizon,
                outcome_at=row.outcome_at,
                asset_price=row.asset_price,
                benchmark_price=row.benchmark_price,
                asset_return_pct=row.asset_return_pct,
                benchmark_return_pct=row.benchmark_return_pct,
                excess_return_pct=row.excess_return_pct,
                directionally_correct=row.directionally_correct,
                estimated_cost_pct=row.estimated_cost_pct,
            )
            for row in self.session.scalars(statement).all()
        ]

    @staticmethod
    def _episode(row: EvaluationEpisodeRow) -> EvaluationEpisode:
        return EvaluationEpisode(
            decision_id=row.decision_id,
            symbol=row.symbol,
            action=row.action,
            decision_at=row.decision_at,
            data_as_of=row.data_as_of,
            reference_price=row.reference_price,
            benchmark_symbol=row.benchmark_symbol,
            benchmark_price=row.benchmark_price,
            confidence=row.confidence,
            experiment_id=row.experiment_id,
            metadata=row.metadata_payload or {},
        )


class PostgresAdmissionPolicy:
    def __init__(
        self,
        session: Session,
        *,
        cooldown_seconds: int = 86_400,
        material_price_move_pct: float = 3.0,
        daily_token_budget: int = 0,
    ):
        self.session = session
        self.cooldown_seconds = max(0, int(cooldown_seconds))
        self.material_price_move_pct = max(0.0, float(material_price_move_pct))
        self.daily_token_budget = max(0, int(daily_token_budget))

    def try_admit(
        self,
        symbol: str,
        *,
        price: Optional[float] = None,
        estimated_tokens: int = 0,
        now: Optional[datetime] = None,
    ) -> AdmissionDecision:
        symbol = symbol.upper().strip()
        now = now or _utcnow()
        if now.tzinfo is None:
            raise ValueError("now must include a timezone")
        row = self.session.scalar(
            select(AnalysisAdmissionRow)
            .where(AnalysisAdmissionRow.symbol == symbol)
            .with_for_update()
        )
        if row is not None and row.in_flight:
            return AdmissionDecision(
                allowed=False, reason="analysis already in flight", symbol=symbol
            )
        day = now.astimezone(timezone.utc).date().isoformat()
        tokens_used = (
            int(row.tokens_used or 0)
            if row is not None and row.token_day == day
            else 0
        )
        requested_tokens = max(0, estimated_tokens)
        if self.daily_token_budget and tokens_used + requested_tokens > self.daily_token_budget:
            return AdmissionDecision(
                allowed=False, reason="daily token budget exceeded", symbol=symbol
            )
        if row is not None and row.last_admitted_at:
            cooling_down = now < row.last_admitted_at + timedelta(
                seconds=self.cooldown_seconds
            )
            material_move = bool(
                price
                and row.last_price
                and row.last_price > 0
                and abs((price / row.last_price - 1.0) * 100.0)
                >= self.material_price_move_pct
            )
            if cooling_down and not material_move:
                return AdmissionDecision(
                    allowed=False, reason="symbol cooldown active", symbol=symbol
                )
        if row is None:
            row = AnalysisAdmissionRow(symbol=symbol)
            self.session.add(row)
        row.last_admitted_at = now
        if price is not None:
            row.last_price = price
        row.in_flight = True
        row.token_day = day
        row.tokens_used = tokens_used + requested_tokens
        self.session.flush()
        return AdmissionDecision(
            allowed=True, reason="admitted", symbol=symbol, admitted_at=now
        )

    def complete(self, symbol: str) -> None:
        row = self.session.scalar(
            select(AnalysisAdmissionRow)
            .where(AnalysisAdmissionRow.symbol == symbol.upper().strip())
            .with_for_update()
        )
        if row is not None:
            row.in_flight = False
            self.session.flush()


class PostgresOutboxRepository:
    def __init__(self, session: Session):
        self.session = session

    def enqueue(
        self,
        topic: str,
        *,
        idempotency_key: str,
        payload: dict[str, Any],
        available_at: Optional[datetime] = None,
    ) -> str:
        existing = self.session.scalar(
            select(OutboxRow).where(OutboxRow.idempotency_key == idempotency_key)
        )
        if existing is not None:
            self._validate_idempotent_content(existing, topic, payload)
            return existing.outbox_id
        now = _utcnow()
        row = OutboxRow(
            outbox_id=str(uuid4()),
            topic=topic,
            idempotency_key=idempotency_key,
            payload=payload,
            created_at=now,
            available_at=available_at or now,
            attempts=0,
        )
        try:
            with self.session.begin_nested():
                self.session.add(row)
                self.session.flush()
        except IntegrityError:
            existing = self.session.scalar(
                select(OutboxRow).where(
                    OutboxRow.idempotency_key == idempotency_key
                )
            )
            if existing is None:
                raise
            self._validate_idempotent_content(existing, topic, payload)
            return existing.outbox_id
        return row.outbox_id

    def claim(
        self,
        *,
        worker_id: str,
        limit: int = 10,
        lease_seconds: int = 60,
        now: Optional[datetime] = None,
    ) -> list[OutboxMessage]:
        now = now or _utcnow()
        rows = self.session.scalars(
            select(OutboxRow)
            .where(
                OutboxRow.processed_at.is_(None),
                OutboxRow.dead_lettered_at.is_(None),
                OutboxRow.available_at <= now,
                (OutboxRow.locked_until.is_(None)) | (OutboxRow.locked_until < now),
            )
            .order_by(OutboxRow.available_at, OutboxRow.created_at)
            .limit(max(1, int(limit)))
            .with_for_update(skip_locked=True)
        ).all()
        locked_until = now + timedelta(seconds=max(1, int(lease_seconds)))
        for row in rows:
            row.locked_by = worker_id
            row.locked_until = locked_until
            row.attempts += 1
        self.session.flush()
        return [self._message(row) for row in rows]

    def mark_processed(
        self,
        outbox_id: str,
        *,
        worker_id: str,
        processed_at: Optional[datetime] = None,
    ) -> None:
        row = self._locked_message(outbox_id, worker_id)
        row.processed_at = processed_at or _utcnow()
        row.locked_by = None
        row.locked_until = None
        row.last_error = None
        self.session.flush()

    def mark_failed(
        self,
        outbox_id: str,
        *,
        worker_id: str,
        error: str,
        retry_at: datetime,
        dead_letter: bool = False,
    ) -> None:
        row = self._locked_message(outbox_id, worker_id)
        row.last_error = error[:4000]
        row.available_at = retry_at
        row.dead_lettered_at = _utcnow() if dead_letter else None
        row.locked_by = None
        row.locked_until = None
        self.session.flush()

    def _locked_message(self, outbox_id: str, worker_id: str) -> OutboxRow:
        row = self.session.scalar(
            select(OutboxRow)
            .where(OutboxRow.outbox_id == outbox_id)
            .with_for_update()
        )
        if row is None or row.locked_by != worker_id:
            raise OutboxLeaseLost(f"Worker {worker_id} no longer owns {outbox_id}")
        return row

    @staticmethod
    def _validate_idempotent_content(
        row: OutboxRow, topic: str, payload: dict[str, Any]
    ) -> None:
        if row.topic != topic or row.payload != payload:
            raise ValueError(
                "outbox idempotency key already exists with different content"
            )

    @staticmethod
    def _message(row: OutboxRow) -> OutboxMessage:
        return OutboxMessage(
            outbox_id=row.outbox_id,
            topic=row.topic,
            idempotency_key=row.idempotency_key,
            payload=row.payload,
            created_at=row.created_at,
            available_at=row.available_at,
            locked_until=row.locked_until,
            locked_by=row.locked_by,
            attempts=row.attempts,
        )


class PostgresOrderLedger:
    TERMINAL = {
        BrokerOrderStatus.FILLED.value,
        BrokerOrderStatus.CANCELED.value,
        BrokerOrderStatus.REJECTED.value,
        BrokerOrderStatus.EXPIRED.value,
    }

    def __init__(self, session: Session):
        self.session = session

    def record_submission(self, result: ExecutionResult) -> list[BrokerOrderRecord]:
        now = _utcnow()
        keys = result.plan.metadata.get("leg_idempotency_keys", [])
        serialized_actions = result.model_dump(mode="json")["actions"]
        records = []
        for index, leg in enumerate(result.plan.legs):
            if leg.action == PlanAction.HOLD:
                continue
            existing = self.session.scalar(
                select(BrokerOrderRow).where(
                    BrokerOrderRow.decision_id == result.decision_id,
                    BrokerOrderRow.leg_index == index,
                )
            )
            if existing is not None:
                records.append(self._record(existing))
                continue
            action = serialized_actions[index] if index < len(serialized_actions) else {}
            raw = action.get("result", action)
            remote_id = raw.get("order_id")
            client_id = (
                keys[index]
                if index < len(keys)
                else raw.get("client_order_id") or f"{result.decision_id}-{index}"
            )
            status = str(raw.get("status") or "submitted").lower()
            row = BrokerOrderRow(
                order_key=str(uuid4()),
                decision_id=result.decision_id,
                leg_index=index,
                broker=result.gateway,
                broker_order_id=str(remote_id) if remote_id is not None else None,
                client_order_id=client_id,
                symbol=result.symbol,
                side=leg.side or leg.action.value.lower(),
                status=status,
                requested_quantity=leg.quantity,
                requested_notional=leg.notional_usd,
                filled_quantity=float(raw.get("filled_quantity") or 0),
                filled_avg_price=raw.get("filled_avg_price"),
                submitted_at=now,
                updated_at=now,
                raw=raw,
            )
            self.session.add(row)
            self.session.add(
                BrokerOrderTransitionRow(
                    order_key=row.order_key,
                    observed_at=now,
                    from_status=None,
                    to_status=status,
                    filled_quantity=row.filled_quantity,
                    filled_avg_price=row.filled_avg_price,
                    raw=raw,
                )
            )
            records.append(self._record(row))
        if self.session.get(ReconciliationLeaseRow, result.decision_id) is None:
            self.session.add(
                ReconciliationLeaseRow(
                    decision_id=result.decision_id,
                    broker=result.gateway,
                    available_at=now,
                    attempts=0,
                    updated_at=now,
                )
            )
        self.session.flush()
        return records

    def apply_snapshot(
        self,
        *,
        decision_id: str,
        leg_index: int,
        snapshot: BrokerOrderSnapshot,
        observed_at: Optional[datetime] = None,
    ) -> BrokerOrderRecord:
        observed_at = observed_at or _utcnow()
        row = self.session.scalar(
            select(BrokerOrderRow)
            .where(
                BrokerOrderRow.decision_id == decision_id,
                BrokerOrderRow.leg_index == leg_index,
            )
            .with_for_update()
        )
        if row is None:
            raise KeyError(f"Unknown broker order {decision_id} leg {leg_index}")
        previous_status = row.status
        previous_quantity = row.filled_quantity
        delta = snapshot.filled_quantity - previous_quantity
        if delta < -1e-9:
            raise ValueError("broker filled quantity cannot decrease")
        if delta > 1e-9 and snapshot.filled_avg_price is not None:
            previous_notional = (row.filled_avg_price or 0.0) * previous_quantity
            cumulative_notional = snapshot.filled_avg_price * snapshot.filled_quantity
            fill_price = (cumulative_notional - previous_notional) / delta
            sequence = self.session.scalar(
                select(func.count(BrokerFillRow.fill_key)).where(
                    BrokerFillRow.order_key == row.order_key
                )
            ) or 0
            self.session.add(
                BrokerFillRow(
                    fill_key=str(uuid4()),
                    order_key=row.order_key,
                    fill_sequence=sequence + 1,
                    quantity=delta,
                    price=fill_price,
                    observed_at=observed_at,
                    source="reconciliation_snapshot",
                )
            )
        row.broker_order_id = snapshot.order_id
        row.status = snapshot.status.value
        row.filled_quantity = snapshot.filled_quantity
        row.filled_avg_price = snapshot.filled_avg_price
        row.updated_at = observed_at
        if row.status in self.TERMINAL:
            row.terminal_at = observed_at
        if previous_status != row.status or delta > 1e-9:
            self.session.add(
                BrokerOrderTransitionRow(
                    order_key=row.order_key,
                    observed_at=observed_at,
                    from_status=previous_status,
                    to_status=row.status,
                    filled_quantity=row.filled_quantity,
                    filled_avg_price=row.filled_avg_price,
                    raw=snapshot.model_dump(mode="json"),
                )
            )
        self.session.flush()
        return self._record(row)

    def orders_for_decision(self, decision_id: str) -> list[BrokerOrderRecord]:
        rows = self.session.scalars(
            select(BrokerOrderRow)
            .where(BrokerOrderRow.decision_id == decision_id)
            .order_by(BrokerOrderRow.leg_index)
        ).all()
        return [self._record(row) for row in rows]

    @staticmethod
    def _record(row: BrokerOrderRow) -> BrokerOrderRecord:
        return BrokerOrderRecord(
            order_key=row.order_key,
            decision_id=row.decision_id,
            leg_index=row.leg_index,
            broker=row.broker,
            broker_order_id=row.broker_order_id,
            client_order_id=row.client_order_id,
            symbol=row.symbol,
            side=row.side,
            status=row.status,
            requested_quantity=row.requested_quantity,
            requested_notional=row.requested_notional,
            filled_quantity=row.filled_quantity,
            filled_avg_price=row.filled_avg_price,
            submitted_at=row.submitted_at,
            updated_at=row.updated_at,
            terminal_at=row.terminal_at,
        )


class PostgresReconciliationQueue:
    def __init__(self, session: Session):
        self.session = session

    def claim(
        self,
        *,
        worker_id: str,
        limit: int = 25,
        lease_seconds: int = 60,
        now: Optional[datetime] = None,
    ) -> list[ReconciliationTask]:
        now = now or _utcnow()
        rows = self.session.scalars(
            select(ReconciliationLeaseRow)
            .where(
                ReconciliationLeaseRow.completed_at.is_(None),
                ReconciliationLeaseRow.available_at <= now,
                or_(
                    ReconciliationLeaseRow.locked_until.is_(None),
                    ReconciliationLeaseRow.locked_until < now,
                ),
            )
            .order_by(
                ReconciliationLeaseRow.available_at,
                ReconciliationLeaseRow.decision_id,
            )
            .limit(max(1, int(limit)))
            .with_for_update(skip_locked=True)
        ).all()
        tasks = []
        for row in rows:
            lifecycle = self.session.get(LifecycleRow, row.decision_id)
            row.locked_by = worker_id
            row.locked_until = now + timedelta(seconds=max(1, int(lease_seconds)))
            row.attempts += 1
            row.updated_at = now
            tasks.append(
                ReconciliationTask(
                    decision_id=row.decision_id,
                    broker=row.broker,
                    attempts=row.attempts,
                    execution_result=(lifecycle.result or {}) if lifecycle else {},
                )
            )
        self.session.flush()
        return tasks

    def complete(
        self,
        decision_id: str,
        *,
        worker_id: str,
        now: Optional[datetime] = None,
    ) -> None:
        row = self._locked(decision_id, worker_id)
        now = now or _utcnow()
        row.completed_at = now
        row.updated_at = now
        row.locked_by = None
        row.locked_until = None
        row.last_error = None
        self.session.flush()

    def retry(
        self,
        decision_id: str,
        *,
        worker_id: str,
        retry_at: datetime,
        error: Optional[str] = None,
    ) -> None:
        row = self._locked(decision_id, worker_id)
        row.available_at = retry_at
        row.updated_at = _utcnow()
        row.locked_by = None
        row.locked_until = None
        row.last_error = (error or "")[:4000] or None
        self.session.flush()

    def _locked(
        self, decision_id: str, worker_id: str
    ) -> ReconciliationLeaseRow:
        row = self.session.scalar(
            select(ReconciliationLeaseRow)
            .where(ReconciliationLeaseRow.decision_id == decision_id)
            .with_for_update()
        )
        if row is None or row.locked_by != worker_id:
            raise ReconciliationLeaseLost(
                f"Worker {worker_id} no longer owns reconciliation {decision_id}"
            )
        return row


class PostgresPortfolioReservationRepository:
    def __init__(self, session: Session):
        self.session = session

    def reserve(
        self,
        batch: PortfolioDecisionBatch,
        *,
        account_key: str,
        ttl_seconds: int = 300,
        now: Optional[datetime] = None,
    ) -> PortfolioReservation:
        now = now or _utcnow()
        self._lock_account(account_key)
        existing = self.session.scalar(
            select(PortfolioReservationRow).where(
                PortfolioReservationRow.batch_id == batch.batch_id
            )
        )
        if existing is not None:
            if existing.account_key != account_key:
                raise ValueError("portfolio batch is already reserved for another account")
            return self._record(existing)
        self._expire_due(account_key=account_key, now=now)
        outstanding = self.session.scalar(
            select(
                func.coalesce(func.sum(PortfolioReservationRow.reserved_notional), 0.0)
            ).where(
                PortfolioReservationRow.account_key == account_key,
                PortfolioReservationRow.status.in_(
                    [ReservationStatus.RESERVED.value, ReservationStatus.CONSUMED.value]
                ),
            )
        ) or 0.0
        available = (
            max(0.0, batch.gross_limit_usd - batch.starting_gross_exposure_usd - outstanding)
            if batch.gross_limit_usd is not None
            else float("inf")
        )
        outstanding_by_symbol = dict(
            self.session.execute(
                select(
                    PortfolioReservationAllocationRow.symbol_key,
                    func.sum(PortfolioReservationAllocationRow.approved_notional),
                )
                .join(
                    PortfolioReservationRow,
                    PortfolioReservationRow.reservation_id
                    == PortfolioReservationAllocationRow.reservation_id,
                )
                .where(
                    PortfolioReservationRow.account_key == account_key,
                    PortfolioReservationRow.status.in_(
                        [
                            ReservationStatus.RESERVED.value,
                            ReservationStatus.CONSUMED.value,
                        ]
                    ),
                    PortfolioReservationAllocationRow.state.in_(
                        [
                            AllocationReservationState.RESERVED.value,
                            AllocationReservationState.CONSUMED.value,
                        ]
                    ),
                )
                .group_by(PortfolioReservationAllocationRow.symbol_key)
            ).all()
        )
        adjusted = batch.model_copy(deep=True)
        additions = sorted(
            (
                row
                for row in adjusted.allocations
                if row.status == BatchAllocationStatus.APPROVED
            ),
            key=lambda row: (row.priority, row.symbol, row.decision_id),
        )
        reserved = 0.0
        for allocation in additions:
            symbol_key = _symbol_key(allocation.symbol)
            symbol_available = float("inf")
            if adjusted.max_symbol_concentration_pct is not None:
                symbol_limit = (
                    adjusted.account_equity_usd
                    * adjusted.max_symbol_concentration_pct
                    / 100.0
                )
                symbol_available = max(
                    0.0,
                    symbol_limit
                    - adjusted.starting_symbol_exposure_usd.get(symbol_key, 0.0)
                    - outstanding_by_symbol.get(symbol_key, 0.0),
                )
            approved = min(
                allocation.approved_notional_usd,
                max(0.0, available),
                symbol_available,
            )
            if approved < allocation.approved_notional_usd:
                allocation.reasons.append(
                    "Outstanding portfolio or symbol reservations clipped this allocation."
                )
            allocation.approved_notional_usd = approved
            if approved <= 0:
                allocation.status = BatchAllocationStatus.BLOCKED
            available -= approved
            outstanding_by_symbol[symbol_key] = (
                outstanding_by_symbol.get(symbol_key, 0.0) + approved
            )
            reserved += approved
        adjusted.ending_reserved_exposure_usd = (
            adjusted.starting_gross_exposure_usd + reserved
        )
        expires_at = now + timedelta(seconds=max(1, int(ttl_seconds)))
        row = PortfolioReservationRow(
            reservation_id=str(uuid4()),
            batch_id=adjusted.batch_id,
            account_key=account_key,
            snapshot_hash=adjusted.snapshot_hash,
            status=ReservationStatus.RESERVED.value,
            starting_gross_exposure=adjusted.starting_gross_exposure_usd,
            gross_limit=adjusted.gross_limit_usd,
            reserved_notional=reserved,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
            payload=adjusted.model_dump(mode="json"),
        )
        self.session.add(row)
        self.session.flush()
        self.session.add(
            PortfolioReservationTransitionRow(
                reservation_id=row.reservation_id,
                recorded_at=now,
                from_status=None,
                to_status=ReservationStatus.RESERVED.value,
                payload={"reserved_notional_usd": reserved},
            )
        )
        for allocation in additions:
            if allocation.approved_notional_usd <= 0:
                continue
            self.session.add(
                PortfolioReservationAllocationRow(
                    allocation_id=str(uuid4()),
                    reservation_id=row.reservation_id,
                    decision_id=allocation.decision_id,
                    symbol=allocation.symbol,
                    symbol_key=_symbol_key(allocation.symbol),
                    priority=allocation.priority,
                    state=AllocationReservationState.RESERVED.value,
                    approved_notional=allocation.approved_notional_usd,
                )
            )
        self.session.flush()
        return self._record(row)

    def consume(
        self,
        reservation_id: str,
        *,
        decision_id: str,
        now: Optional[datetime] = None,
    ) -> PortfolioReservation:
        now = now or _utcnow()
        row = self._locked(reservation_id)
        allocation = self.session.scalar(
            select(PortfolioReservationAllocationRow).where(
                PortfolioReservationAllocationRow.reservation_id == reservation_id,
                PortfolioReservationAllocationRow.decision_id == decision_id,
            )
        )
        if allocation is None:
            raise KeyError(f"Decision {decision_id} is not reserved by {reservation_id}")
        if allocation.state == AllocationReservationState.RELEASED.value:
            raise ValueError("released allocation cannot be consumed")
        if allocation.state == AllocationReservationState.CONSUMED.value:
            return self._record(row)
        allocation.state = AllocationReservationState.CONSUMED.value
        allocation.consumed_at = allocation.consumed_at or now
        self.session.flush()
        remaining = self.session.scalar(
            select(func.count()).select_from(PortfolioReservationAllocationRow).where(
                PortfolioReservationAllocationRow.reservation_id == reservation_id,
                PortfolioReservationAllocationRow.state
                == AllocationReservationState.RESERVED.value,
            )
        ) or 0
        if remaining == 0:
            self._transition(row, ReservationStatus.CONSUMED, now)
        self.session.flush()
        return self._record(row)

    def release(
        self, reservation_id: str, *, now: Optional[datetime] = None
    ) -> PortfolioReservation:
        now = now or _utcnow()
        row = self._locked(reservation_id)
        if row.status in {ReservationStatus.RELEASED.value, ReservationStatus.EXPIRED.value}:
            return self._record(row)
        allocations = self.session.scalars(
            select(PortfolioReservationAllocationRow).where(
                PortfolioReservationAllocationRow.reservation_id == reservation_id
            )
        ).all()
        for allocation in allocations:
            allocation.state = AllocationReservationState.RELEASED.value
        row.released_at = now
        self._transition(row, ReservationStatus.RELEASED, now)
        self.session.flush()
        return self._record(row)

    def expire_due(self, now: Optional[datetime] = None) -> int:
        return self._expire_due(account_key=None, now=now or _utcnow())

    def _expire_due(self, *, account_key: Optional[str], now: datetime) -> int:
        statement = select(PortfolioReservationRow).where(
            PortfolioReservationRow.status == ReservationStatus.RESERVED.value,
            PortfolioReservationRow.expires_at <= now,
        )
        if account_key is not None:
            statement = statement.where(
                PortfolioReservationRow.account_key == account_key
            )
        rows = self.session.scalars(statement.with_for_update()).all()
        for row in rows:
            allocations = self.session.scalars(
                select(PortfolioReservationAllocationRow).where(
                    PortfolioReservationAllocationRow.reservation_id
                    == row.reservation_id
                )
            ).all()
            for allocation in allocations:
                if allocation.state == AllocationReservationState.RESERVED.value:
                    allocation.state = AllocationReservationState.RELEASED.value
            self._transition(row, ReservationStatus.EXPIRED, now)
        self.session.flush()
        return len(rows)

    def _lock_account(self, account_key: str) -> None:
        if self.session.bind is not None and self.session.bind.dialect.name == "postgresql":
            self.session.execute(
                select(
                    func.pg_advisory_xact_lock(func.hashtextextended(account_key, 17))
                )
            )

    def _locked(self, reservation_id: str) -> PortfolioReservationRow:
        row = self.session.scalar(
            select(PortfolioReservationRow)
            .where(PortfolioReservationRow.reservation_id == reservation_id)
            .with_for_update()
        )
        if row is None:
            raise KeyError(f"Unknown portfolio reservation {reservation_id}")
        return row

    def _transition(
        self, row: PortfolioReservationRow, status: ReservationStatus, now: datetime
    ) -> None:
        previous = row.status
        row.status = status.value
        row.updated_at = now
        self.session.add(
            PortfolioReservationTransitionRow(
                reservation_id=row.reservation_id,
                recorded_at=now,
                from_status=previous,
                to_status=status.value,
                payload={},
            )
        )

    @staticmethod
    def _record(row: PortfolioReservationRow) -> PortfolioReservation:
        return PortfolioReservation(
            reservation_id=row.reservation_id,
            account_key=row.account_key,
            status=ReservationStatus(row.status),
            reserved_notional_usd=row.reserved_notional,
            created_at=row.created_at,
            updated_at=row.updated_at,
            expires_at=row.expires_at,
            batch=PortfolioDecisionBatch.model_validate(row.payload),
        )
