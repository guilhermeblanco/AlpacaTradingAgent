"""Configured integrations as named instances rather than one slot each."""

from alembic import op
import sqlalchemy as sa


revision = "20260910_13"
down_revision = "20260910_12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Deliberately additive. Plain vault entries — the one-slot-per-
    # provider arrangement this replaces — still resolve underneath, so a
    # deployment configured before this keeps working and can be moved
    # across one instance at a time rather than all at once.
    op.create_table(
        "integrations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=60), nullable=False),
        sa.Column("provider", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column(
            "active", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(length=160), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_integrations_kind_active", "integrations", ["kind", "active"])


def downgrade() -> None:
    op.drop_index("ix_integrations_kind_active", table_name="integrations")
    op.drop_table("integrations")
