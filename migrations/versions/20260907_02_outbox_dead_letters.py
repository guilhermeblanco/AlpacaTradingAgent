"""Add dead-letter state to the transactional outbox."""

from alembic import op
import sqlalchemy as sa


revision = "20260907_02"
down_revision = "20260907_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "outbox", sa.Column("dead_lettered_at", sa.DateTime(timezone=True))
    )


def downgrade() -> None:
    op.drop_column("outbox", "dead_lettered_at")
