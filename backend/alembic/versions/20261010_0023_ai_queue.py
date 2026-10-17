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

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Idempotent on purpose: an earlier draft of this revision (without ``ai_reviewed_flip``) may already have been
    # applied to a database; adding what is missing is then enough, and the backfill below is harmless to repeat.
    op.execute("ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS ai_for_analysis_id UUID")
    op.execute("ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS ai_reviewed_flip SMALLINT")
    op.execute("ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS ai_attempts SMALLINT NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS ai_next_attempt_at TIMESTAMPTZ")
    op.execute("ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS ai_last_error VARCHAR(48)")
    # Until now ``ai_provider = 'claude'`` meant "reviewed by the model after the last analysis" (any new
    # analysis put it back to 'rules'), so those rows are reviewed for their current analysis.
    op.execute(
        "UPDATE opportunities SET ai_for_analysis_id = analysis_id, ai_reviewed_flip = flip_score"
        " WHERE ai_provider IN ('claude', 'gemini') AND analysis_id IS NOT NULL AND ai_reviewed_flip IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_opportunities_ai_queue ON opportunities (flip_score)"
        " WHERE is_active AND ai_for_analysis_id IS DISTINCT FROM analysis_id"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_opportunities_ai_queue")
    for col in (
        "ai_last_error",
        "ai_next_attempt_at",
        "ai_attempts",
        "ai_reviewed_flip",
        "ai_for_analysis_id",
    ):
        op.execute(f"ALTER TABLE opportunities DROP COLUMN IF EXISTS {col}")
