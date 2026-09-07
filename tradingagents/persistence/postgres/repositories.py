"""Session-bound PostgreSQL implementations of persistence ports."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tradingagents.evaluation.models import EvaluationEpisode, EvaluationOutcome
from tradingagents.evaluation.point_in_time import validate_episode_point_in_time
from tradingagents.lifecycle.models import LifecycleRecord, LifecycleStatus
from tradingagents.operations.admission import AdmissionDecision
from tradingagents.persistence.events import EventEnvelope

from .models import (
    AnalysisAdmissionRow,
    DecisionEventRow,
    EvaluationEpisodeRow,
    EvaluationOutcomeRow,
    LifecycleRow,
    LifecycleTransitionRow,
    OutboxRow,
)
from tradingagents.persistence.outbox import OutboxLeaseLost, OutboxMessage


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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

    def get_episode(self, decision_id: str) -> Optional[EvaluationEpisode]:
        row = self.session.get(EvaluationEpisodeRow, decision_id)
        if row is None:
            return None
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

    def record_outcome(self, outcome: EvaluationOutcome) -> None:
        if self.get_episode(outcome.decision_id) is None:
            raise KeyError(f"Unknown evaluation episode {outcome.decision_id}")
        row = self.session.scalar(
            select(EvaluationOutcomeRow).where(
                EvaluationOutcomeRow.decision_id == outcome.decision_id,
                EvaluationOutcomeRow.horizon == outcome.horizon,
            )
        )
        values = outcome.model_dump()
        if row is None:
            row = EvaluationOutcomeRow(**values)
            self.session.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
        self.session.flush()

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
