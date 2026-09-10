"""SQLAlchemy projections for the first PostgreSQL persistence increment."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")
SEQUENCE_ID = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    pass


class DecisionEventRow(Base):
    __tablename__ = "decision_events"
    __table_args__ = (
        UniqueConstraint(
            "aggregate_type",
            "aggregate_id",
            "aggregate_version",
            name="uq_decision_event_aggregate_version",
        ),
        UniqueConstraint("idempotency_key", name="uq_decision_event_idempotency"),
        CheckConstraint("aggregate_version >= 1", name="ck_event_aggregate_version"),
        CheckConstraint("schema_version >= 1", name="ck_event_schema_version"),
        Index("ix_decision_events_aggregate", "aggregate_type", "aggregate_id"),
        Index("ix_decision_events_recorded_at", "recorded_at"),
    )

    sequence_id: Mapped[int] = mapped_column(SEQUENCE_ID, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(80), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(160), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    correlation_id: Mapped[Optional[str]] = mapped_column(String(160))
    causation_id: Mapped[Optional[str]] = mapped_column(String(36))
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(200))
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class LifecycleRow(Base):
    __tablename__ = "lifecycle"
    __table_args__ = (Index("ix_lifecycle_status_updated", "status", "updated_at"),)

    decision_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    run_id: Mapped[Optional[str]] = mapped_column(String(160))
    error: Mapped[Optional[str]] = mapped_column(Text)
    result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON_DOCUMENT)
    metadata_payload: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON_DOCUMENT, nullable=False, default=dict
    )


class LifecycleTransitionRow(Base):
    __tablename__ = "lifecycle_transitions"
    __table_args__ = (
        Index("ix_lifecycle_transitions_decision", "decision_id", "transition_id"),
    )

    transition_id: Mapped[int] = mapped_column(SEQUENCE_ID, primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(
        ForeignKey("lifecycle.decision_id", ondelete="CASCADE"), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    from_status: Mapped[Optional[str]] = mapped_column(String(40))
    to_status: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False, default=dict)


class EvaluationEpisodeRow(Base):
    __tablename__ = "evaluation_episodes"
    __table_args__ = (
        CheckConstraint("reference_price > 0", name="ck_episode_reference_price"),
        CheckConstraint("benchmark_price > 0", name="ck_episode_benchmark_price"),
        Index("ix_evaluation_experiment", "experiment_id", "decision_at"),
    )

    decision_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(80), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    decision_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reference_price: Mapped[float] = mapped_column(Float, nullable=False)
    benchmark_symbol: Mapped[str] = mapped_column(String(80), nullable=False)
    benchmark_price: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    experiment_id: Mapped[str] = mapped_column(String(160), nullable=False)
    metadata_payload: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON_DOCUMENT, nullable=False, default=dict
    )


class EvaluationOutcomeRow(Base):
    __tablename__ = "evaluation_outcomes"
    __table_args__ = (
        UniqueConstraint("decision_id", "horizon", name="pk_evaluation_outcome"),
    )

    outcome_id: Mapped[int] = mapped_column(SEQUENCE_ID, primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(
        ForeignKey("evaluation_episodes.decision_id", ondelete="CASCADE"), nullable=False
    )
    horizon: Mapped[str] = mapped_column(String(40), nullable=False)
    outcome_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    asset_price: Mapped[float] = mapped_column(Float, nullable=False)
    benchmark_price: Mapped[float] = mapped_column(Float, nullable=False)
    asset_return_pct: Mapped[float] = mapped_column(Float, nullable=False)
    benchmark_return_pct: Mapped[float] = mapped_column(Float, nullable=False)
    excess_return_pct: Mapped[float] = mapped_column(Float, nullable=False)
    directionally_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    estimated_cost_pct: Mapped[float] = mapped_column(Float, nullable=False)


class AnalysisAdmissionRow(Base):
    __tablename__ = "analysis_admission"

    symbol: Mapped[str] = mapped_column(String(80), primary_key=True)
    last_admitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_price: Mapped[Optional[float]] = mapped_column(Float)
    in_flight: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    token_day: Mapped[Optional[str]] = mapped_column(String(10))
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class OutboxRow(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_outbox_idempotency"),
        Index("ix_outbox_claim", "available_at", "processed_at", "locked_until"),
        CheckConstraint("attempts >= 0", name="ck_outbox_attempts"),
    )

    outbox_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    topic: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[Optional[str]] = mapped_column(String(160))
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    dead_lettered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text)


class BrokerOrderRow(Base):
    __tablename__ = "broker_orders"
    __table_args__ = (
        UniqueConstraint("broker", "client_order_id", name="uq_broker_order_client"),
        UniqueConstraint("broker", "broker_order_id", name="uq_broker_order_remote"),
        UniqueConstraint("decision_id", "leg_index", name="uq_broker_order_leg"),
        Index("ix_broker_orders_status_updated", "status", "updated_at"),
    )

    order_key: Mapped[str] = mapped_column(String(36), primary_key=True)
    decision_id: Mapped[str] = mapped_column(
        ForeignKey("lifecycle.decision_id", ondelete="CASCADE"), nullable=False
    )
    leg_index: Mapped[int] = mapped_column(Integer, nullable=False)
    broker: Mapped[str] = mapped_column(String(80), nullable=False)
    broker_order_id: Mapped[Optional[str]] = mapped_column(String(160))
    client_order_id: Mapped[str] = mapped_column(String(200), nullable=False)
    symbol: Mapped[str] = mapped_column(String(80), nullable=False)
    side: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    requested_quantity: Mapped[Optional[float]] = mapped_column(Float)
    requested_notional: Mapped[Optional[float]] = mapped_column(Float)
    filled_quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    filled_avg_price: Mapped[Optional[float]] = mapped_column(Float)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    terminal_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False, default=dict)


class BrokerOrderTransitionRow(Base):
    __tablename__ = "broker_order_transitions"
    __table_args__ = (
        Index("ix_broker_order_transitions_order", "order_key", "transition_id"),
    )

    transition_id: Mapped[int] = mapped_column(SEQUENCE_ID, primary_key=True, autoincrement=True)
    order_key: Mapped[str] = mapped_column(
        ForeignKey("broker_orders.order_key", ondelete="CASCADE"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    from_status: Mapped[Optional[str]] = mapped_column(String(40))
    to_status: Mapped[str] = mapped_column(String(40), nullable=False)
    filled_quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    filled_avg_price: Mapped[Optional[float]] = mapped_column(Float)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False, default=dict)


class BrokerFillRow(Base):
    __tablename__ = "broker_fills"
    __table_args__ = (
        UniqueConstraint("order_key", "fill_sequence", name="uq_broker_fill_sequence"),
    )

    fill_key: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_key: Mapped[str] = mapped_column(
        ForeignKey("broker_orders.order_key", ondelete="CASCADE"), nullable=False
    )
    fill_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False)


class ReconciliationLeaseRow(Base):
    __tablename__ = "reconciliation_leases"
    __table_args__ = (
        Index(
            "ix_reconciliation_leases_available",
            "completed_at",
            "available_at",
            "locked_until",
        ),
    )

    decision_id: Mapped[str] = mapped_column(
        ForeignKey("lifecycle.decision_id", ondelete="CASCADE"), primary_key=True
    )
    broker: Mapped[str] = mapped_column(String(80), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    locked_by: Mapped[Optional[str]] = mapped_column(String(160))
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PortfolioReservationRow(Base):
    __tablename__ = "portfolio_reservations"
    __table_args__ = (
        Index("ix_portfolio_reservations_account_status", "account_key", "status"),
    )

    reservation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    account_key: Mapped[str] = mapped_column(String(200), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    starting_gross_exposure: Mapped[float] = mapped_column(Float, nullable=False)
    gross_limit: Mapped[Optional[float]] = mapped_column(Float)
    reserved_notional: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)


class PortfolioReservationAllocationRow(Base):
    __tablename__ = "portfolio_reservation_allocations"
    __table_args__ = (
        UniqueConstraint("reservation_id", "decision_id", name="uq_reservation_decision"),
        Index("ix_reservation_allocations_state", "reservation_id", "state"),
        Index("ix_reservation_allocations_symbol_state", "symbol_key", "state"),
    )

    allocation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    reservation_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_reservations.reservation_id", ondelete="CASCADE"),
        nullable=False,
    )
    decision_id: Mapped[str] = mapped_column(String(160), nullable=False)
    symbol: Mapped[str] = mapped_column(String(80), nullable=False)
    symbol_key: Mapped[str] = mapped_column(String(80), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    approved_notional: Mapped[float] = mapped_column(Float, nullable=False)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[Optional[str]] = mapped_column(String(160))
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class PortfolioReservationTransitionRow(Base):
    __tablename__ = "portfolio_reservation_transitions"
    __table_args__ = (
        Index("ix_reservation_transitions_reservation", "reservation_id", "transition_id"),
    )

    transition_id: Mapped[int] = mapped_column(SEQUENCE_ID, primary_key=True, autoincrement=True)
    reservation_id: Mapped[str] = mapped_column(
        ForeignKey("portfolio_reservations.reservation_id", ondelete="CASCADE"),
        nullable=False,
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    from_status: Mapped[Optional[str]] = mapped_column(String(40))
    to_status: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False, default=dict)


class ServiceControlRow(Base):
    __tablename__ = "service_controls"

    service: Mapped[str] = mapped_column(String(120), primary_key=True)
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[Optional[str]] = mapped_column(String(160))
    #: When somebody last asked this service to restart. A worker that
    #: started before this exits; the container runtime brings it back.
    #: A request rather than a command, because the web process has no
    #: way to signal a sibling container and should not be given one.
    restart_requested_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )


class ServiceHeartbeatRow(Base):
    __tablename__ = "service_heartbeats"
    __table_args__ = (Index("ix_service_heartbeats_last_seen", "last_seen_at"),)

    service: Mapped[str] = mapped_column(String(120), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False, default=dict)


class SafetyStateRow(Base):
    __tablename__ = "safety_state"

    scope: Mapped[str] = mapped_column(String(200), primary_key=True)
    kill_switch_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    kill_switch_reason: Mapped[Optional[str]] = mapped_column(Text)
    kill_switch_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    high_water_mark: Mapped[Optional[float]] = mapped_column(Float)
    consecutive_rejections: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AccountSnapshotBaselineRow(Base):
    __tablename__ = "account_snapshot_baselines"

    broker: Mapped[str] = mapped_column(String(80), primary_key=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    source_decision_id: Mapped[Optional[str]] = mapped_column(String(160))
    mismatch_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_report: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON_DOCUMENT)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class SafetyTokenUsageRow(Base):
    __tablename__ = "safety_token_usage"

    scope: Mapped[str] = mapped_column(String(200), primary_key=True)
    usage_day: Mapped[date] = mapped_column(Date, primary_key=True)
    tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class IntegrationRow(Base):
    """One configured third party, as an instance rather than a slot.

    Several may exist per kind — two Alpaca accounts, a production model
    key and an evaluation one — and exactly one is active for the kinds
    where only one can answer. Credentials live in the vault under this
    row's id, so two instances of one provider never overwrite each
    other.
    """

    __tablename__ = "integrations"
    __table_args__ = (Index("ix_integrations_kind_active", "kind", "active"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[Optional[str]] = mapped_column(String(160))


class RuntimeSettingRow(Base):
    """A setting an operator changed, which outranks the environment.

    Deliberately separate from the credential vault: these are not
    secrets, they are decisions, and they are worth reading in plain text
    when someone asks why the worker is armed.
    """

    __tablename__ = "runtime_settings"

    scope: Mapped[str] = mapped_column(String(120), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(160), nullable=False)


class RuntimeSettingAuditRow(Base):
    """Every change, with who and what it was before.

    A UI that can arm a trading system needs to be able to answer "who
    turned this on, and when" without anyone having to remember.
    """

    __tablename__ = "runtime_setting_audit"
    __table_args__ = (
        Index("ix_runtime_setting_audit_scope_time", "scope", "occurred_at"),
    )

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    previous_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    actor: Mapped[str] = mapped_column(String(160), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class IntegrationCredentialRow(Base):
    __tablename__ = "integration_credentials"

    scope: Mapped[str] = mapped_column(String(120), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IntegrationCredentialAuditRow(Base):
    __tablename__ = "integration_credential_audit"
    __table_args__ = (
        Index("ix_integration_credential_audit_scope_time", "scope", "occurred_at"),
    )

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope: Mapped[str] = mapped_column(String(120), nullable=False)
    credential_name: Mapped[str] = mapped_column(String(120), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    actor: Mapped[str] = mapped_column(String(160), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
