"""Add operator-changeable runtime settings and their audit history."""

from alembic import op
import sqlalchemy as sa


revision = "20260910_11"
down_revision = "20260909_10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runtime_settings",
        sa.Column("scope", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(length=160), nullable=False),
        sa.PrimaryKeyConstraint("scope", "name"),
    )
    # Plain text on purpose. These are decisions, not secrets — the
    # credential vault next door is where the secrets live — and being
    # able to read them with psql is the point when someone asks why the
    # worker is armed.
    op.create_table(
        "runtime_setting_audit",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("scope", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("previous_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(length=160), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        "ix_runtime_setting_audit_scope_time",
        "runtime_setting_audit",
        ["scope", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_runtime_setting_audit_scope_time", table_name="runtime_setting_audit"
    )
    op.drop_table("runtime_setting_audit")
    op.drop_table("runtime_settings")
