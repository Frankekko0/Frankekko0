"""v3 phase 5: the decision stored with each opportunity

* ``decision_verdict``        - STRONG_BUY | BUY | NEGOTIATE | WATCHLIST | PASS | INSUFFICIENT_EVIDENCE;
* ``data_completeness_score`` - how much of the listing could be read (the fourth, separate score);
* ``decision``                - the whole decision: scores, reasons, warnings, missing information,
  vetoes and the requirements still missing for a STRONG BUY.

``verdict`` (BUY / CONSIDER / SKIP) stays, derived from the decision, for the readers that still
use the three-valued vocabulary. Rows analysed before this migration have no decision (NULL): none
is invented, they get one the next time they are analysed.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("opportunities", sa.Column("decision_verdict", sa.String(24), nullable=True))
    op.add_column("opportunities", sa.Column("data_completeness_score", sa.SmallInteger(), nullable=True))
    op.add_column("opportunities", sa.Column("decision", postgresql.JSONB(), nullable=True))
    op.create_index("ix_opportunities_decision_verdict", "opportunities", ["decision_verdict"])
    op.create_check_constraint(
        "decision_verdict_values",
        "opportunities",
        "decision_verdict IS NULL OR decision_verdict IN "
        "('STRONG_BUY','BUY','NEGOTIATE','WATCHLIST','PASS','INSUFFICIENT_EVIDENCE')",
    )


def downgrade() -> None:
    op.drop_constraint("decision_verdict_values", "opportunities", type_="check")
    op.drop_index("ix_opportunities_decision_verdict", table_name="opportunities")
    op.drop_column("opportunities", "decision")
    op.drop_column("opportunities", "data_completeness_score")
    op.drop_column("opportunities", "decision_verdict")
