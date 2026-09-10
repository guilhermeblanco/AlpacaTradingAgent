"""Allow a restart to be requested for a background service."""

from alembic import op
import sqlalchemy as sa


revision = "20260910_12"
down_revision = "20260910_11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A request, not a command. The web process cannot signal a sibling
    # container and should not be handed a podman socket to try; a worker
    # that started before this timestamp exits on its own and the
    # container's restart policy brings it back.
    op.add_column(
        "service_controls",
        sa.Column("restart_requested_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("service_controls", "restart_requested_at")
