"""Add per-allocation execution claims."""

from alembic import op
import sqlalchemy as sa


revision = "20260908_06"
down_revision = "20260908_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portfolio_reservation_allocations",
        sa.Column("locked_by", sa.String(160)),
    )
    op.add_column(
        "portfolio_reservation_allocations",
        sa.Column("locked_until", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_reservation_allocations_dispatch_claim",
        "portfolio_reservation_allocations",
        ["state", "locked_until"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reservation_allocations_dispatch_claim",
        table_name="portfolio_reservation_allocations",
    )
    op.drop_column("portfolio_reservation_allocations", "locked_until")
    op.drop_column("portfolio_reservation_allocations", "locked_by")
