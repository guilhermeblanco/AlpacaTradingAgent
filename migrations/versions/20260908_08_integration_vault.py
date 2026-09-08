"""Add encrypted integration credential storage and audit history."""

from alembic import op
import sqlalchemy as sa


revision = "20260908_08"
down_revision = "20260908_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integration_credentials",
        sa.Column("scope", sa.String(120), primary_key=True),
        sa.Column("name", sa.String(120), primary_key=True),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "integration_credential_audit",
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column("scope", sa.String(120), nullable=False),
        sa.Column("credential_name", sa.String(120), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("actor", sa.String(160), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_integration_credential_audit_scope_time",
        "integration_credential_audit",
        ["scope", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_integration_credential_audit_scope_time",
        table_name="integration_credential_audit",
    )
    op.drop_table("integration_credential_audit")
    op.drop_table("integration_credentials")
