"""phase 2.4: "to verify" listings

``listings.status_before_verify`` keeps what a listing was read as before it went stale
(``status = 'to_verify'``, set by the worker, see ``app.tracking.verification``). Listings are
not touched by the migration: the first run of the job marks the ones that are too old.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("listings", sa.Column("status_before_verify", sa.String(16), nullable=True))


def downgrade() -> None:
    # Back to what each listing was before: the shape of the old schema has no "to verify".
    op.execute(
        "UPDATE listings SET status = coalesce(status_before_verify, 'active') WHERE status = 'to_verify'"
    )
    op.drop_column("listings", "status_before_verify")
