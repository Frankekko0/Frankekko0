"""v3 phase 4b: the dossier of each opportunity

* ``dossier``                 - the versioned record of everything known about the listing: the
  results of the twelve passes (P0-P12), signals typed observed / declared / inferred, the
  contradictions found, what could not be analysed and why, the five main reasons;
* ``analysis_coverage_score`` - the share of the applicable passes that ran with enough data.

Rows analysed before this migration have none (NULL); they get one when next analysed. The permanent
record in ``analyses`` keeps a compact form inside its ``decision`` block.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("opportunities", sa.Column("analysis_coverage_score", sa.SmallInteger(), nullable=True))
    op.add_column("opportunities", sa.Column("dossier", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("opportunities", "dossier")
    op.drop_column("opportunities", "analysis_coverage_score")
