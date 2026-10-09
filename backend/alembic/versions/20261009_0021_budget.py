"""v3 phase 7: capital rules for the purchase plan

The whole budget and how many items to hold at once, both optional. Additive and reversible.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user_preferences", sa.Column("total_budget", sa.Numeric(12, 2), nullable=True))
    op.add_column("user_preferences", sa.Column("max_owned_items", sa.SmallInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_preferences", "max_owned_items")
    op.drop_column("user_preferences", "total_budget")
