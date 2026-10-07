"""marketplace actions: Vinted favourites and purchases started from FlipFinder

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "marketplace_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("value", sa.Boolean(), nullable=True),
        sa.Column("price", sa.Numeric(12, 2), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_marketplace_actions_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listings.id"],
            name="fk_marketplace_actions_listing_id_listings",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_marketplace_actions"),
    )
    op.create_index(
        "ix_marketplace_actions_lookup",
        "marketplace_actions",
        ["user_id", "listing_id", "kind", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_marketplace_actions_lookup", table_name="marketplace_actions")
    op.drop_table("marketplace_actions")
