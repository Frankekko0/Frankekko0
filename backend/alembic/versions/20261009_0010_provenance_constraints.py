"""phase 2.1: publication date provenance and non-empty URL

* ``listings.published_at_kind`` says how the publication date is known (exact / relative /
  reported / unknown). Rows whose "publication date" equals the first observation were stamped
  with the moment we first saw them, not a real date: the value is dropped (nothing is lost, the
  same instant is in ``first_seen_at``) and ``days_to_sell`` computed from it is cleared. The
  sale statistics keep working from the observed window (first seen -> sold).
* a listing cannot have an empty URL.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    empty = conn.execute(sa.text("SELECT count(*) FROM listings WHERE btrim(url) = ''")).scalar_one()
    if empty:
        raise RuntimeError(
            f"{empty} listing(s) have an empty URL: fix or remove them before migrating (no URL is invented)."
        )

    op.add_column(
        "listings",
        sa.Column("published_at_kind", sa.String(8), server_default="unknown", nullable=False),
    )
    op.execute("UPDATE listings SET published_at_kind = 'reported' WHERE published_at IS NOT NULL")
    # Stamped with the observation instant: not a publication date.
    op.execute(
        """
        UPDATE listings
           SET published_at = NULL, published_at_kind = 'unknown', days_to_sell = NULL
         WHERE published_at IS NOT NULL
           AND abs(extract(epoch FROM (published_at - first_seen_at))) < 1
        """
    )
    op.execute(
        """
        UPDATE sold_sales s
           SET published_at = NULL, days_to_sell = NULL
          FROM listings l
         WHERE s.listing_id = l.id AND l.published_at IS NULL AND s.published_at IS NOT NULL
        """
    )
    op.create_check_constraint(
        "published_at_kind_valid",
        "listings",
        "published_at_kind IN ('exact', 'relative', 'reported', 'unknown')",
    )
    op.create_check_constraint("url_not_empty", "listings", "btrim(url) <> ''")


def downgrade() -> None:
    op.drop_constraint("url_not_empty", "listings", type_="check")
    op.drop_constraint("published_at_kind_valid", "listings", type_="check")
    op.drop_column("listings", "published_at_kind")
