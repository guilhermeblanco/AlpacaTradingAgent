"""Add shared safety and kill-switch state."""

from alembic import op
import sqlalchemy as sa


revision = "20260908_09"
down_revision = "20260908_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "safety_state",
        sa.Column("scope", sa.String(200), primary_key=True),
        sa.Column("kill_switch_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("kill_switch_reason", sa.Text()),
        sa.Column("kill_switch_changed_at", sa.DateTime(timezone=True)),
        sa.Column("high_water_mark", sa.Float()),
        sa.Column("consecutive_rejections", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "safety_token_usage",
        sa.Column("scope", sa.String(200), primary_key=True),
        sa.Column("usage_day", sa.Date(), primary_key=True),
        sa.Column("tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.CheckConstraint("tokens >= 0", name="ck_safety_token_usage_nonnegative"),
    )


def downgrade() -> None:
    op.drop_table("safety_token_usage")
    op.drop_table("safety_state")
