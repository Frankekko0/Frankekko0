"""analysis: one-line headline of the main reason

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("opportunities") as b:
        b.add_column(sa.Column("headline", sa.String(200), nullable=True))
    # Rows analysed before this version get their headline at the next analysis.


def downgrade() -> None:
    with op.batch_alter_table("opportunities") as b:
        b.drop_column("headline")
