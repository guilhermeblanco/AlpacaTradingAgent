"""Add durable broker order transitions and observed fills."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260907_03"
down_revision = "20260907_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "broker_orders",
        sa.Column("order_key", sa.String(36), primary_key=True),
        sa.Column("decision_id", sa.String(160), sa.ForeignKey("lifecycle.decision_id", ondelete="CASCADE"), nullable=False),
        sa.Column("leg_index", sa.Integer(), nullable=False),
        sa.Column("broker", sa.String(80), nullable=False),
        sa.Column("broker_order_id", sa.String(160)),
        sa.Column("client_order_id", sa.String(200), nullable=False),
        sa.Column("symbol", sa.String(80), nullable=False),
        sa.Column("side", sa.String(20), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("requested_quantity", sa.Float()),
        sa.Column("requested_notional", sa.Float()),
        sa.Column("filled_quantity", sa.Float(), nullable=False, server_default="0"),
        sa.Column("filled_avg_price", sa.Float()),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminal_at", sa.DateTime(timezone=True)),
        sa.Column("raw", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint("broker", "client_order_id", name="uq_broker_order_client"),
        sa.UniqueConstraint("broker", "broker_order_id", name="uq_broker_order_remote"),
        sa.UniqueConstraint("decision_id", "leg_index", name="uq_broker_order_leg"),
    )
    op.create_index("ix_broker_orders_status_updated", "broker_orders", ["status", "updated_at"])
    op.create_table(
        "broker_order_transitions",
        sa.Column("transition_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("order_key", sa.String(36), sa.ForeignKey("broker_orders.order_key", ondelete="CASCADE"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("from_status", sa.String(40)),
        sa.Column("to_status", sa.String(40), nullable=False),
        sa.Column("filled_quantity", sa.Float(), nullable=False, server_default="0"),
        sa.Column("filled_avg_price", sa.Float()),
        sa.Column("raw", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_broker_order_transitions_order", "broker_order_transitions", ["order_key", "transition_id"])
    op.create_table(
        "broker_fills",
        sa.Column("fill_key", sa.String(36), primary_key=True),
        sa.Column("order_key", sa.String(36), sa.ForeignKey("broker_orders.order_key", ondelete="CASCADE"), nullable=False),
        sa.Column("fill_sequence", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(80), nullable=False),
        sa.UniqueConstraint("order_key", "fill_sequence", name="uq_broker_fill_sequence"),
    )


def downgrade() -> None:
    op.drop_table("broker_fills")
    op.drop_index("ix_broker_order_transitions_order", table_name="broker_order_transitions")
    op.drop_table("broker_order_transitions")
    op.drop_index("ix_broker_orders_status_updated", table_name="broker_orders")
    op.drop_table("broker_orders")
