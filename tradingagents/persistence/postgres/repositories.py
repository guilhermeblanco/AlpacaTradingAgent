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
from tradingagents.operations.control_plane import (
    OperationalHealth,
    ServiceControl,
    ServiceHeartbeat,
)
from tradingagents.operations.explorer import (
    DecisionActivityDetail,
    DecisionActivitySummary,
    DecisionTimelineEvent,
)
from tradingagents.persistence.events import EventEnvelope
from tradingagents.workbench.analysis_record import ANALYSIS_STAGES_EVENT
from tradingagents.workbench.tape import (
    DecisionTape,
    TapeEvent,
    build_tape,
    stage_for_event,
)
from tradingagents.execution.gates import GateLedger, ledger_from_payload
from tradingagents.execution.models import ExecutionResult, PlanAction
from tradingagents.execution.order_ledger import BrokerOrderRecord
from tradingagents.execution.reconciliation import BrokerOrderSnapshot, BrokerOrderStatus
from tradingagents.execution.reconciliation_queue import (
    ReconciliationLeaseLost,
    ReconciliationTask,
)
from tradingagents.execution.account_monitor import AccountBaseline, AccountDriftReport
from tradingagents.broker.models import PortfolioSnapshot
from tradingagents.portfolio.batch import BatchAllocationStatus, PortfolioDecisionBatch
from tradingagents.portfolio.reservations import (
    AllocationReservationState,
    PortfolioReservation,
    ReservationStatus,
)

from .models import (
    AnalysisAdmissionRow,
    AccountSnapshotBaselineRow,
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
    ServiceControlRow,
    ServiceHeartbeatRow,
)
from tradingagents.persistence.outbox import OutboxLeaseLost, OutboxMessage


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


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

    def experiment_ids(self) -> list[str]:
        """Distinct experiment ids that have recorded at least one episode."""
        return list(
            self.session.scalars(
                select(EvaluationEpisodeRow.experiment_id)
                .distinct()
                .order_by(EvaluationEpisodeRow.experiment_id)
            ).all()
        )

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
            cooling_down = _as_utc(now) < _as_utc(row.last_admitted_at) + timedelta(
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
                    last_error=row.last_error,
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
                func.coalesce(
                    func.sum(PortfolioReservationAllocationRow.approved_notional), 0.0
                )
            )
            .join(
                PortfolioReservationRow,
                PortfolioReservationRow.reservation_id
                == PortfolioReservationAllocationRow.reservation_id,
            )
            .where(
                PortfolioReservationRow.account_key == account_key,
                PortfolioReservationRow.status.in_(
                    [ReservationStatus.RESERVED.value, ReservationStatus.CONSUMED.value]
                ),
                PortfolioReservationAllocationRow.state.in_(
                    [
                        AllocationReservationState.RESERVED.value,
                        AllocationReservationState.DISPATCHING.value,
                        AllocationReservationState.CONSUMED.value,
                    ]
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
                            AllocationReservationState.DISPATCHING.value,
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
        worker_id: Optional[str] = None,
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
        if worker_id is not None and (
            allocation.state != AllocationReservationState.DISPATCHING.value
            or allocation.locked_by != worker_id
        ):
            raise ValueError(
                f"Worker {worker_id} does not own allocation {decision_id}"
            )
        allocation.state = AllocationReservationState.CONSUMED.value
        allocation.consumed_at = allocation.consumed_at or now
        allocation.locked_by = None
        allocation.locked_until = None
        self.session.flush()
        self._refresh_parent(row, now=now)
        self.session.flush()
        return self._record(row)

    def claim_allocation(
        self,
        reservation_id: str,
        *,
        decision_id: str,
        worker_id: str,
        lease_seconds: int = 60,
        now: Optional[datetime] = None,
    ) -> bool:
        now = now or _utcnow()
        row = self._locked(reservation_id)
        if row.status != ReservationStatus.RESERVED.value:
            return False
        allocation = self.session.scalar(
            select(PortfolioReservationAllocationRow)
            .where(
                PortfolioReservationAllocationRow.reservation_id == reservation_id,
                PortfolioReservationAllocationRow.decision_id == decision_id,
            )
            .with_for_update()
        )
        if allocation is None:
            raise KeyError(f"Decision {decision_id} is not reserved by {reservation_id}")
        claimable = allocation.state == AllocationReservationState.RESERVED.value or (
            allocation.state == AllocationReservationState.DISPATCHING.value
            and allocation.locked_until is not None
            and _as_utc(allocation.locked_until) <= _as_utc(now)
        )
        if not claimable:
            return False
        allocation.state = AllocationReservationState.DISPATCHING.value
        allocation.locked_by = worker_id
        allocation.locked_until = now + timedelta(seconds=max(1, int(lease_seconds)))
        row.updated_at = now
        self.session.flush()
        return True

    def release_allocation(
        self,
        reservation_id: str,
        *,
        decision_id: str,
        worker_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> PortfolioReservation:
        now = now or _utcnow()
        row = self._locked(reservation_id)
        allocation = self.session.scalar(
            select(PortfolioReservationAllocationRow)
            .where(
                PortfolioReservationAllocationRow.reservation_id == reservation_id,
                PortfolioReservationAllocationRow.decision_id == decision_id,
            )
            .with_for_update()
        )
        if allocation is None:
            raise KeyError(f"Decision {decision_id} is not reserved by {reservation_id}")
        if allocation.state == AllocationReservationState.RELEASED.value:
            return self._record(row)
        if worker_id is not None and (
            allocation.state != AllocationReservationState.DISPATCHING.value
            or allocation.locked_by != worker_id
        ):
            raise ValueError(
                f"Worker {worker_id} does not own allocation {decision_id}"
            )
        allocation.state = AllocationReservationState.RELEASED.value
        allocation.locked_by = None
        allocation.locked_until = None
        self.session.flush()
        self._refresh_parent(row, now=now)
        self.session.flush()
        return self._record(row)

    def release_decision(
        self, decision_id: str, *, now: Optional[datetime] = None
    ) -> bool:
        now = now or _utcnow()
        allocation = self.session.scalar(
            select(PortfolioReservationAllocationRow)
            .where(PortfolioReservationAllocationRow.decision_id == decision_id)
            .with_for_update()
        )
        if allocation is None or allocation.state == AllocationReservationState.RELEASED.value:
            return False
        row = self._locked(allocation.reservation_id)
        allocation.state = AllocationReservationState.RELEASED.value
        allocation.locked_by = None
        allocation.locked_until = None
        self.session.flush()
        self._refresh_parent(row, now=now)
        self.session.flush()
        return True

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
            allocation.locked_by = None
            allocation.locked_until = None
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
                if allocation.state == AllocationReservationState.RESERVED.value or (
                    allocation.state == AllocationReservationState.DISPATCHING.value
                    and allocation.locked_until is not None
                    and _as_utc(allocation.locked_until) <= _as_utc(now)
                ):
                    allocation.state = AllocationReservationState.RELEASED.value
                    allocation.locked_by = None
                    allocation.locked_until = None
            self.session.flush()
            self._refresh_parent(row, now=now, empty_status=ReservationStatus.EXPIRED)
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

    def _refresh_parent(
        self,
        row: PortfolioReservationRow,
        *,
        now: datetime,
        empty_status: ReservationStatus = ReservationStatus.RELEASED,
    ) -> None:
        states = set(
            self.session.scalars(
                select(PortfolioReservationAllocationRow.state).where(
                    PortfolioReservationAllocationRow.reservation_id
                    == row.reservation_id
                )
            ).all()
        )
        if states & {
            AllocationReservationState.RESERVED.value,
            AllocationReservationState.DISPATCHING.value,
        }:
            status = ReservationStatus.RESERVED
        elif AllocationReservationState.CONSUMED.value in states:
            status = ReservationStatus.CONSUMED
        else:
            status = empty_status
        if row.status != status.value:
            if status in {ReservationStatus.RELEASED, ReservationStatus.EXPIRED}:
                row.released_at = now
            self._transition(row, status, now)

    def _record(self, row: PortfolioReservationRow) -> PortfolioReservation:
        states = dict(
            self.session.execute(
                select(
                    PortfolioReservationAllocationRow.decision_id,
                    PortfolioReservationAllocationRow.state,
                ).where(
                    PortfolioReservationAllocationRow.reservation_id
                    == row.reservation_id
                )
            ).all()
        )
        return PortfolioReservation(
            reservation_id=row.reservation_id,
            account_key=row.account_key,
            status=ReservationStatus(row.status),
            reserved_notional_usd=row.reserved_notional,
            created_at=row.created_at,
            updated_at=row.updated_at,
            expires_at=row.expires_at,
            batch=PortfolioDecisionBatch.model_validate(row.payload),
            allocation_states={
                decision_id: AllocationReservationState(state)
                for decision_id, state in states.items()
            },
        )


class PostgresDecisionExplorerRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_decisions(
        self,
        *,
        limit: int = 100,
        symbol: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[DecisionActivitySummary]:
        statement = select(LifecycleRow).order_by(LifecycleRow.created_at.desc())
        if symbol:
            statement = statement.where(LifecycleRow.symbol == symbol.upper().strip())
        if status:
            statement = statement.where(LifecycleRow.status == status.lower().strip())
        rows = self.session.scalars(statement.limit(max(1, min(limit, 500)))).all()
        if not rows:
            return []
        decision_ids = [row.decision_id for row in rows]
        orders = self.session.scalars(
            select(BrokerOrderRow).where(BrokerOrderRow.decision_id.in_(decision_ids))
        ).all()
        by_decision: dict[str, list[BrokerOrderRow]] = {}
        for order in orders:
            by_decision.setdefault(order.decision_id, []).append(order)
        return [
            DecisionActivitySummary(
                decision_id=row.decision_id,
                symbol=row.symbol,
                status=row.status,
                created_at=row.created_at,
                updated_at=row.updated_at,
                broker=(by_decision[row.decision_id][0].broker if by_decision.get(row.decision_id) else None),
                order_count=len(by_decision.get(row.decision_id, [])),
                filled_quantity=sum(order.filled_quantity for order in by_decision.get(row.decision_id, [])),
                error=row.error,
            )
            for row in rows
        ]

    def get_decision(self, decision_id: str) -> DecisionActivityDetail:
        summaries = [
            item
            for item in self.list_decisions(limit=500)
            if item.decision_id == decision_id
        ]
        if not summaries:
            raise KeyError(f"Unknown decision {decision_id}")
        events: list[DecisionTimelineEvent] = []
        for row in self.session.scalars(
            select(LifecycleTransitionRow).where(
                LifecycleTransitionRow.decision_id == decision_id
            )
        ).all():
            events.append(
                DecisionTimelineEvent(
                    occurred_at=row.recorded_at,
                    category="lifecycle",
                    label=f"{row.from_status or 'new'} -> {row.to_status}",
                    status=row.to_status,
                    details=row.payload or {},
                )
            )
        for row in self.session.scalars(
            select(DecisionEventRow).where(
                DecisionEventRow.aggregate_type == "decision",
                DecisionEventRow.aggregate_id == decision_id,
            )
        ).all():
            events.append(
                DecisionTimelineEvent(
                    occurred_at=row.occurred_at,
                    category="decision",
                    label=row.event_type,
                    details=row.payload or {},
                )
            )
        orders = self.session.scalars(
            select(BrokerOrderRow).where(BrokerOrderRow.decision_id == decision_id)
        ).all()
        for order in orders:
            events.append(
                DecisionTimelineEvent(
                    occurred_at=order.submitted_at,
                    category="order",
                    label=f"{order.broker} {order.side} {order.symbol}",
                    status=order.status,
                    details={
                        "order_id": order.broker_order_id,
                        "requested_quantity": order.requested_quantity,
                        "requested_notional": order.requested_notional,
                    },
                )
            )
            for fill in self.session.scalars(
                select(BrokerFillRow).where(BrokerFillRow.order_key == order.order_key)
            ).all():
                events.append(
                    DecisionTimelineEvent(
                        occurred_at=fill.observed_at,
                        category="fill",
                        label=f"Filled {fill.quantity:g} @ {fill.price:g}",
                        status="filled",
                        details={"source": fill.source},
                    )
                )
        for outcome in self.session.scalars(
            select(EvaluationOutcomeRow).where(
                EvaluationOutcomeRow.decision_id == decision_id
            )
        ).all():
            events.append(
                DecisionTimelineEvent(
                    occurred_at=outcome.outcome_at,
                    category="outcome",
                    label=f"{outcome.horizon} outcome",
                    status="correct" if outcome.directionally_correct else "incorrect",
                    details={
                        "asset_return_pct": outcome.asset_return_pct,
                        "benchmark_return_pct": outcome.benchmark_return_pct,
                        "excess_return_pct": outcome.excess_return_pct,
                        "estimated_cost_pct": outcome.estimated_cost_pct,
                    },
                )
            )
        events.sort(key=lambda item: item.occurred_at)
        return DecisionActivityDetail(summary=summaries[0], timeline=events)


class PostgresAccountSnapshotRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, broker: str) -> Optional[AccountBaseline]:
        row = self.session.get(AccountSnapshotBaselineRow, broker.lower().strip())
        if row is None:
            return None
        return AccountBaseline(
            broker=row.broker,
            snapshot=PortfolioSnapshot.model_validate(row.snapshot),
            source=row.source,
            source_decision_id=row.source_decision_id,
            mismatch_count=row.mismatch_count,
            updated_at=row.updated_at,
            checked_at=row.checked_at,
        )

    def upsert(
        self,
        broker: str,
        snapshot: PortfolioSnapshot,
        *,
        source: str,
        source_decision_id: Optional[str] = None,
        mismatch_count: int = 0,
    ) -> AccountBaseline:
        key = broker.lower().strip()
        row = self.session.get(AccountSnapshotBaselineRow, key)
        now = _utcnow()
        if row is None:
            row = AccountSnapshotBaselineRow(
                broker=key, snapshot={}, source=source, updated_at=now
            )
            self.session.add(row)
        row.snapshot = snapshot.model_dump(mode="json")
        row.source = source
        row.source_decision_id = source_decision_id
        row.mismatch_count = mismatch_count
        row.updated_at = now
        self.session.flush()
        return self.get(key)

    def record_check(
        self,
        broker: str,
        report: AccountDriftReport,
        *,
        mismatch_count: int,
    ) -> None:
        row = self.session.get(AccountSnapshotBaselineRow, broker.lower().strip())
        if row is None:
            raise KeyError(f"No account baseline for {broker}")
        row.mismatch_count = mismatch_count
        row.last_report = report.model_dump(mode="json")
        row.checked_at = report.checked_at
        self.session.flush()


class PostgresOperationalRepository:
    def __init__(self, session: Session):
        self.session = session

    def control(self, service: str) -> ServiceControl:
        key = service.lower().strip()
        row = self.session.get(ServiceControlRow, key)
        if row is None:
            return ServiceControl(service=key)
        return self._control(row)

    def is_paused(self, service: str) -> bool:
        return self.control(service).paused

    def set_paused(
        self,
        service: str,
        *,
        paused: bool,
        reason: Optional[str] = None,
        updated_by: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> ServiceControl:
        key = service.lower().strip()
        if not key:
            raise ValueError("service name is required")
        now = now or _utcnow()
        row = self.session.scalar(
            select(ServiceControlRow)
            .where(ServiceControlRow.service == key)
            .with_for_update()
        )
        if row is None:
            row = ServiceControlRow(service=key, paused=paused, updated_at=now)
            self.session.add(row)
        row.paused = bool(paused)
        row.reason = reason if paused else None
        row.updated_at = now
        row.updated_by = updated_by
        self.session.flush()
        return self._control(row)

    def beat(
        self,
        service: str,
        *,
        instance_id: str,
        status: str = "healthy",
        details: Optional[dict[str, Any]] = None,
        now: Optional[datetime] = None,
    ) -> ServiceHeartbeat:
        service_key = service.lower().strip()
        instance_key = instance_id.strip()
        if not service_key or not instance_key:
            raise ValueError("service and instance_id are required")
        now = now or _utcnow()
        row = self.session.get(ServiceHeartbeatRow, (service_key, instance_key))
        if row is None:
            row = ServiceHeartbeatRow(
                service=service_key,
                instance_id=instance_key,
                status=status,
                last_seen_at=now,
                details=details or {},
            )
            self.session.add(row)
        else:
            row.status = status
            row.last_seen_at = now
            row.details = details or {}
        self.session.flush()
        return self._heartbeat(row, now=now, stale_after_seconds=float("inf"))

    def health(
        self,
        *,
        now: Optional[datetime] = None,
        stale_after_seconds: float = 600,
    ) -> OperationalHealth:
        now = now or _utcnow()
        controls = [
            self._control(row)
            for row in self.session.scalars(
                select(ServiceControlRow).order_by(ServiceControlRow.service)
            ).all()
        ]
        heartbeats = [
            self._heartbeat(
                row, now=now, stale_after_seconds=max(0.0, stale_after_seconds)
            )
            for row in self.session.scalars(
                select(ServiceHeartbeatRow).order_by(
                    ServiceHeartbeatRow.service, ServiceHeartbeatRow.instance_id
                )
            ).all()
        ]
        reconciliation_pending = self.session.scalar(
            select(func.count()).select_from(ReconciliationLeaseRow).where(
                ReconciliationLeaseRow.completed_at.is_(None)
            )
        ) or 0
        oldest_reconciliation = self.session.scalar(
            select(func.min(ReconciliationLeaseRow.available_at)).where(
                ReconciliationLeaseRow.completed_at.is_(None)
            )
        )
        outbox_pending = self.session.scalar(
            select(func.count()).select_from(OutboxRow).where(
                OutboxRow.processed_at.is_(None),
                OutboxRow.dead_lettered_at.is_(None),
            )
        ) or 0
        outbox_dead = self.session.scalar(
            select(func.count()).select_from(OutboxRow).where(
                OutboxRow.dead_lettered_at.is_not(None)
            )
        ) or 0
        in_flight = self.session.scalar(
            select(func.count()).select_from(AnalysisAdmissionRow).where(
                AnalysisAdmissionRow.in_flight.is_(True)
            )
        ) or 0
        active_reservations = self.session.scalar(
            select(func.count())
            .select_from(PortfolioReservationAllocationRow)
            .where(
                PortfolioReservationAllocationRow.state.in_(
                    [
                        AllocationReservationState.RESERVED.value,
                        AllocationReservationState.DISPATCHING.value,
                        AllocationReservationState.CONSUMED.value,
                    ]
                )
            )
        ) or 0
        active_executions = self.session.scalar(
            select(func.count()).select_from(LifecycleRow).where(
                LifecycleRow.status.in_(
                    [
                        LifecycleStatus.SUBMITTED.value,
                        LifecycleStatus.PARTIALLY_FILLED.value,
                    ]
                )
            )
        ) or 0
        lag = 0.0
        if oldest_reconciliation is not None:
            lag = max(
                0.0,
                (_as_utc(now) - _as_utc(oldest_reconciliation)).total_seconds(),
            )
        return OperationalHealth(
            observed_at=now,
            controls=controls,
            heartbeats=heartbeats,
            reconciliation_pending=reconciliation_pending,
            reconciliation_oldest_lag_seconds=lag,
            outbox_pending=outbox_pending,
            outbox_dead_lettered=outbox_dead,
            analyses_in_flight=in_flight,
            active_reservation_allocations=active_reservations,
            active_executions=active_executions,
        )

    @staticmethod
    def _control(row: ServiceControlRow) -> ServiceControl:
        return ServiceControl(
            service=row.service,
            paused=row.paused,
            reason=row.reason,
            updated_at=row.updated_at,
            updated_by=row.updated_by,
        )

    @staticmethod
    def _heartbeat(
        row: ServiceHeartbeatRow,
        *,
        now: datetime,
        stale_after_seconds: float,
    ) -> ServiceHeartbeat:
        stale = (
            _as_utc(now) - _as_utc(row.last_seen_at)
        ).total_seconds() > stale_after_seconds
        return ServiceHeartbeat(
            service=row.service,
            instance_id=row.instance_id,
            status=row.status,
            last_seen_at=row.last_seen_at,
            stale=stale,
            details=row.details or {},
        )


class PostgresWorkbenchRepository:
    """Assembles Decision Tapes: the analysis half joined to the execution half.

    Reads only. The two halves are written by different processes at
    different times — the graph records its stages when the analysis
    finishes, the pipeline its gate ledger when the order resolves — and
    they meet here on `decision_id`.
    """

    def __init__(self, session: Session):
        self.session = session
        self.explorer = PostgresDecisionExplorerRepository(session)

    def _events_for(self, decision_ids: list[str]) -> dict[str, list[DecisionEventRow]]:
        if not decision_ids:
            return {}
        rows = self.session.scalars(
            select(DecisionEventRow)
            .where(
                DecisionEventRow.aggregate_type == "decision",
                DecisionEventRow.aggregate_id.in_(decision_ids),
            )
            .order_by(DecisionEventRow.aggregate_version)
        ).all()
        grouped: dict[str, list[DecisionEventRow]] = {}
        for row in rows:
            grouped.setdefault(row.aggregate_id, []).append(row)
        return grouped

    @staticmethod
    def _analysis_from(events: list[DecisionEventRow]) -> dict[str, Any]:
        for row in reversed(events):
            if row.event_type == ANALYSIS_STAGES_EVENT:
                payload = row.payload or {}
                analysis = payload.get("analysis")
                if isinstance(analysis, dict):
                    return analysis
        return {}

    @staticmethod
    def _ledger_from(events: list[DecisionEventRow]) -> Optional[GateLedger]:
        for row in reversed(events):
            if row.event_type == "gate_ledger_recorded":
                ledger = ledger_from_payload(row.payload)
                if ledger is not None:
                    return ledger
        return None

    def _orders_for(self, decision_id: str) -> list[dict[str, Any]]:
        rows = self.session.scalars(
            select(BrokerOrderRow).where(BrokerOrderRow.decision_id == decision_id)
        ).all()
        return [
            {
                "order_key": row.order_key,
                "broker": row.broker,
                "broker_order_id": row.broker_order_id,
                "client_order_id": row.client_order_id,
                "symbol": row.symbol,
                "side": row.side,
                "status": row.status,
                "requested_quantity": row.requested_quantity,
                "requested_notional": row.requested_notional,
                "filled_quantity": row.filled_quantity,
                "filled_avg_price": row.filled_avg_price,
                "submitted_at": row.submitted_at,
            }
            for row in rows
        ]

    def _outcomes_for(self, decision_id: str) -> list[dict[str, Any]]:
        rows = self.session.scalars(
            select(EvaluationOutcomeRow)
            .where(EvaluationOutcomeRow.decision_id == decision_id)
            .order_by(EvaluationOutcomeRow.outcome_at)
        ).all()
        return [
            {
                "horizon": row.horizon,
                "outcome_at": row.outcome_at,
                "asset_return_pct": row.asset_return_pct,
                "benchmark_return_pct": row.benchmark_return_pct,
                "excess_return_pct": row.excess_return_pct,
                "estimated_cost_pct": row.estimated_cost_pct,
                "directionally_correct": row.directionally_correct,
            }
            for row in rows
        ]

    def tape(self, decision_id: str) -> DecisionTape:
        """One decision, every stage, in pipeline order."""
        detail = self.explorer.get_decision(decision_id)
        events = self._events_for([decision_id]).get(decision_id, [])
        orders = self._orders_for(decision_id)
        outcomes = self._outcomes_for(decision_id)

        tape_events = [
            TapeEvent(
                occurred_at=item.occurred_at,
                stage=stage_for_event(item.category, item.label),
                category=item.category,
                label=item.label,
                status=item.status,
                details=item.details,
            )
            for item in detail.timeline
        ]
        return build_tape(
            summary=detail.summary,
            analysis=self._analysis_from(events),
            gate_ledger=self._ledger_from(events),
            orders=orders,
            outcomes=outcomes,
            events=tape_events,
        )

    def board(
        self,
        *,
        limit: int = 50,
        symbol: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[DecisionTape]:
        """Recent decisions as tapes, newest first, for the pipeline board.

        Skips the per-decision timeline: a board card needs the stage a
        decision reached and what stopped it, not every event behind it.
        """
        summaries = self.explorer.list_decisions(
            limit=limit, symbol=symbol, status=status
        )
        if not summaries:
            return []
        grouped = self._events_for([item.decision_id for item in summaries])
        return [
            build_tape(
                summary=summary,
                analysis=self._analysis_from(grouped.get(summary.decision_id, [])),
                gate_ledger=self._ledger_from(grouped.get(summary.decision_id, [])),
                orders=self._orders_for(summary.decision_id),
                outcomes=self._outcomes_for(summary.decision_id),
            )
            for summary in summaries
        ]
