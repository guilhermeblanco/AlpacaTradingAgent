"""Add durable account-wide reconciliation baselines."""

from alembic import op
import sqlalchemy as sa


revision = "20260909_10"
down_revision = "20260908_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_snapshot_baselines",
        sa.Column("broker", sa.String(80), primary_key=True),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("source_decision_id", sa.String(160)),
        sa.Column("mismatch_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_report", sa.JSON()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("account_snapshot_baselines")
