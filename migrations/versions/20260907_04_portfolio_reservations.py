"""Add durable portfolio allocation reservations."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260907_04"
down_revision = "20260907_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "portfolio_reservations",
        sa.Column("reservation_id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), nullable=False, unique=True),
        sa.Column("account_key", sa.String(200), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("starting_gross_exposure", sa.Float(), nullable=False),
        sa.Column("gross_limit", sa.Float()),
        sa.Column("reserved_notional", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("payload", jsonb, nullable=False),
    )
    op.create_index(
        "ix_portfolio_reservations_account_status",
        "portfolio_reservations",
        ["account_key", "status"],
    )
    op.create_table(
        "portfolio_reservation_allocations",
        sa.Column("allocation_id", sa.String(36), primary_key=True),
        sa.Column(
            "reservation_id",
            sa.String(36),
            sa.ForeignKey("portfolio_reservations.reservation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("decision_id", sa.String(160), nullable=False),
        sa.Column("symbol", sa.String(80), nullable=False),
        sa.Column("symbol_key", sa.String(80), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("approved_notional", sa.Float(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "reservation_id", "decision_id", name="uq_reservation_decision"
        ),
    )
    op.create_index(
        "ix_reservation_allocations_state",
        "portfolio_reservation_allocations",
        ["reservation_id", "state"],
    )
    op.create_index(
        "ix_reservation_allocations_symbol_state",
        "portfolio_reservation_allocations",
        ["symbol_key", "state"],
    )
    op.create_table(
        "portfolio_reservation_transitions",
        sa.Column("transition_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "reservation_id",
            sa.String(36),
            sa.ForeignKey("portfolio_reservations.reservation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("from_status", sa.String(40)),
        sa.Column("to_status", sa.String(40), nullable=False),
        sa.Column(
            "payload", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )
    op.create_index(
        "ix_reservation_transitions_reservation",
        "portfolio_reservation_transitions",
        ["reservation_id", "transition_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reservation_transitions_reservation",
        table_name="portfolio_reservation_transitions",
    )
    op.drop_table("portfolio_reservation_transitions")
    op.drop_index(
        "ix_reservation_allocations_symbol_state",
        table_name="portfolio_reservation_allocations",
    )
    op.drop_index(
        "ix_reservation_allocations_state",
        table_name="portfolio_reservation_allocations",
    )
    op.drop_table("portfolio_reservation_allocations")
    op.drop_index(
        "ix_portfolio_reservations_account_status",
        table_name="portfolio_reservations",
    )
    op.drop_table("portfolio_reservations")
