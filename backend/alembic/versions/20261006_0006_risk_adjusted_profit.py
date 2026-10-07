"""risk-adjusted expected profit: ranking key of the opportunities

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("opportunities", sa.Column("risk_adjusted_profit", sa.Numeric(10, 2), nullable=True))
    op.add_column("opportunities", sa.Column("sale_probability", sa.Numeric(8, 4), nullable=True))
    op.add_column("opportunities", sa.Column("authenticity_probability", sa.Numeric(8, 4), nullable=True))
    op.add_column("opportunities", sa.Column("authenticity_verdict", sa.String(24), nullable=True))
    op.create_index(
        "ix_opportunities_active_rap",
        "opportunities",
        ["is_active", sa.text("risk_adjusted_profit DESC NULLS LAST")],
    )


def downgrade() -> None:
    op.drop_index("ix_opportunities_active_rap", table_name="opportunities")
    for col in (
        "authenticity_verdict",
        "authenticity_probability",
        "sale_probability",
        "risk_adjusted_profit",
    ):
        op.drop_column("opportunities", col)
