"""Create durable decision event and operational projection tables."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260907_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "decision_events",
        sa.Column("sequence_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("event_id", sa.String(36), nullable=False, unique=True),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("aggregate_type", sa.String(80), nullable=False),
        sa.Column("aggregate_id", sa.String(160), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.String(160)),
        sa.Column("causation_id", sa.String(36)),
        sa.Column("idempotency_key", sa.String(200)),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload", jsonb, nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.CheckConstraint("aggregate_version >= 1", name="ck_event_aggregate_version"),
        sa.CheckConstraint("schema_version >= 1", name="ck_event_schema_version"),
        sa.UniqueConstraint(
            "aggregate_type", "aggregate_id", "aggregate_version",
            name="uq_decision_event_aggregate_version",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_decision_event_idempotency"),
    )
    op.create_index(
        "ix_decision_events_aggregate", "decision_events",
        ["aggregate_type", "aggregate_id"],
    )
    op.create_index(
        "ix_decision_events_recorded_at", "decision_events", ["recorded_at"]
    )

    op.create_table(
        "lifecycle",
        sa.Column("decision_id", sa.String(160), primary_key=True),
        sa.Column("symbol", sa.String(80), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column("run_id", sa.String(160)),
        sa.Column("error", sa.Text()),
        sa.Column("result", jsonb),
        sa.Column("metadata", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index(
        "ix_lifecycle_status_updated", "lifecycle", ["status", "updated_at"]
    )
    op.create_table(
        "lifecycle_transitions",
        sa.Column("transition_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "decision_id", sa.String(160),
            sa.ForeignKey("lifecycle.decision_id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("from_status", sa.String(40)),
        sa.Column("to_status", sa.String(40), nullable=False),
        sa.Column("payload", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index(
        "ix_lifecycle_transitions_decision", "lifecycle_transitions",
        ["decision_id", "transition_id"],
    )

    op.create_table(
        "evaluation_episodes",
        sa.Column("decision_id", sa.String(160), primary_key=True),
        sa.Column("symbol", sa.String(80), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("decision_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reference_price", sa.Float(), nullable=False),
        sa.Column("benchmark_symbol", sa.String(80), nullable=False),
        sa.Column("benchmark_price", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("experiment_id", sa.String(160), nullable=False),
        sa.Column("metadata", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint("reference_price > 0", name="ck_episode_reference_price"),
        sa.CheckConstraint("benchmark_price > 0", name="ck_episode_benchmark_price"),
    )
    op.create_index(
        "ix_evaluation_experiment", "evaluation_episodes",
        ["experiment_id", "decision_at"],
    )
    op.create_table(
        "evaluation_outcomes",
        sa.Column("outcome_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "decision_id", sa.String(160),
            sa.ForeignKey("evaluation_episodes.decision_id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("horizon", sa.String(40), nullable=False),
        sa.Column("outcome_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("asset_price", sa.Float(), nullable=False),
        sa.Column("benchmark_price", sa.Float(), nullable=False),
        sa.Column("asset_return_pct", sa.Float(), nullable=False),
        sa.Column("benchmark_return_pct", sa.Float(), nullable=False),
        sa.Column("excess_return_pct", sa.Float(), nullable=False),
        sa.Column("directionally_correct", sa.Boolean(), nullable=False),
        sa.Column("estimated_cost_pct", sa.Float(), nullable=False),
        sa.UniqueConstraint("decision_id", "horizon", name="pk_evaluation_outcome"),
    )

    op.create_table(
        "analysis_admission",
        sa.Column("symbol", sa.String(80), primary_key=True),
        sa.Column("last_admitted_at", sa.DateTime(timezone=True)),
        sa.Column("last_price", sa.Float()),
        sa.Column("in_flight", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("token_day", sa.String(10)),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "outbox",
        sa.Column("outbox_id", sa.String(36), primary_key=True),
        sa.Column("topic", sa.String(120), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("payload", jsonb, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("locked_by", sa.String(160)),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.CheckConstraint("attempts >= 0", name="ck_outbox_attempts"),
        sa.UniqueConstraint("idempotency_key", name="uq_outbox_idempotency"),
    )
    op.create_index(
        "ix_outbox_claim", "outbox",
        ["available_at", "processed_at", "locked_until"],
    )

    op.execute(
        """
        CREATE FUNCTION reject_decision_event_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'decision_events is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER decision_events_immutable
        BEFORE UPDATE OR DELETE ON decision_events
        FOR EACH ROW EXECUTE FUNCTION reject_decision_event_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS decision_events_immutable ON decision_events")
    op.execute("DROP FUNCTION IF EXISTS reject_decision_event_mutation()")
    op.drop_index("ix_outbox_claim", table_name="outbox")
    op.drop_table("outbox")
    op.drop_table("analysis_admission")
    op.drop_table("evaluation_outcomes")
    op.drop_index("ix_evaluation_experiment", table_name="evaluation_episodes")
    op.drop_table("evaluation_episodes")
    op.drop_index("ix_lifecycle_transitions_decision", table_name="lifecycle_transitions")
    op.drop_table("lifecycle_transitions")
    op.drop_index("ix_lifecycle_status_updated", table_name="lifecycle")
    op.drop_table("lifecycle")
    op.drop_index("ix_decision_events_recorded_at", table_name="decision_events")
    op.drop_index("ix_decision_events_aggregate", table_name="decision_events")
    op.drop_table("decision_events")
