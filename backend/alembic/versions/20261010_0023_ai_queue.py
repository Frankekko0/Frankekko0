"""The model-analysis queue lives on the opportunity

The strong-model deal analysis is paced to the provider's request caps, so what is still to be analysed has to
survive a restart, a deploy and a day of exhausted quota. The ``opportunities`` row is that queue:

* ``ai_for_analysis_id``: the analysis (``analyses.id``) the stored model review was written for. A row whose
  current analysis differs is waiting for a review.
* ``ai_reviewed_flip``: the flip score of the analysis the review was written for. A small market move keeps the
  review (``ai_for_analysis_id`` follows the new analysis), so the "moved enough for a new review" test has to be
  measured from this, not from the previous row.
* ``ai_attempts``: failed attempts that count (the model answered with something unusable); quota and outages
  do not count.
* ``ai_next_attempt_at``: do not try before (backoff after a deferral, or the lease of a queued job).
* ``ai_last_error``: why the last attempt did not produce a review (``rpd``, ``cooldown``, ``bad_answer``...).

Rows the model has already reviewed keep that review (they are marked as reviewed for their current analysis).
Additive and reversible.

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("opportunities", sa.Column("ai_for_analysis_id", postgresql.UUID(as_uuid=True)))
    op.add_column("opportunities", sa.Column("ai_reviewed_flip", sa.SmallInteger()))
    op.add_column(
        "opportunities", sa.Column("ai_attempts", sa.SmallInteger(), nullable=False, server_default="0")
    )
    op.add_column("opportunities", sa.Column("ai_next_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("opportunities", sa.Column("ai_last_error", sa.String(48)))
    # Until now ``ai_provider = 'claude'`` meant "reviewed by the model after the last analysis" (any new
    # analysis put it back to 'rules'), so those rows are reviewed for their current analysis.
    op.execute(
        "UPDATE opportunities SET ai_for_analysis_id = analysis_id, ai_reviewed_flip = flip_score"
        " WHERE ai_provider IN ('claude', 'gemini') AND analysis_id IS NOT NULL"
    )
    op.create_index(
        "ix_opportunities_ai_queue",
        "opportunities",
        ["flip_score"],
        postgresql_where=sa.text("is_active AND ai_for_analysis_id IS DISTINCT FROM analysis_id"),
    )


def downgrade() -> None:
    op.drop_index("ix_opportunities_ai_queue", table_name="opportunities")
    op.drop_column("opportunities", "ai_last_error")
    op.drop_column("opportunities", "ai_next_attempt_at")
    op.drop_column("opportunities", "ai_attempts")
    op.drop_column("opportunities", "ai_reviewed_flip")
    op.drop_column("opportunities", "ai_for_analysis_id")
