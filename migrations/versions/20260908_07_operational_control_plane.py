"""Add shared service controls and heartbeats."""

from alembic import op
import sqlalchemy as sa


revision = "20260908_07"
down_revision = "20260908_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "service_controls",
        sa.Column("service", sa.String(120), primary_key=True),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(160)),
    )
    op.create_table(
        "service_heartbeats",
        sa.Column("service", sa.String(120), primary_key=True),
        sa.Column("instance_id", sa.String(160), primary_key=True),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_service_heartbeats_last_seen",
        "service_heartbeats",
        ["last_seen_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_service_heartbeats_last_seen", table_name="service_heartbeats")
    op.drop_table("service_heartbeats")
    op.drop_table("service_controls")
