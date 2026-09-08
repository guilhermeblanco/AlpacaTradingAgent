"""Add durable reconciliation worker leases."""

from alembic import op
import sqlalchemy as sa


revision = "20260908_05"
down_revision = "20260907_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reconciliation_leases",
        sa.Column(
            "decision_id",
            sa.String(160),
            sa.ForeignKey("lifecycle.decision_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("broker", sa.String(80), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_by", sa.String(160)),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_reconciliation_leases_available",
        "reconciliation_leases",
        ["completed_at", "available_at", "locked_until"],
    )
    op.execute(
        """INSERT INTO reconciliation_leases
           (decision_id, broker, available_at, attempts, updated_at)
           SELECT decision_id, MIN(broker), CURRENT_TIMESTAMP, 0, CURRENT_TIMESTAMP
           FROM broker_orders
           GROUP BY decision_id
           ON CONFLICT (decision_id) DO NOTHING"""
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reconciliation_leases_available",
        table_name="reconciliation_leases",
    )
    op.drop_table("reconciliation_leases")
