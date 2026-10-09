"""phase 2.3: photos kept over time, repost evidence

* ``listing_images`` gets a stable ``image_key``, first/last seen, ``removed_at`` and ``source``.
  The unique (listing, position) is replaced by one current row per (listing, key): a re-capture
  moves, adds or retires photos instead of deleting and re-inserting them, so copies and hashes
  survive. Two rows of the same listing with the same key (same file listed twice) keep the
  first and retire the others.
* ``listings.duplicate_evidence``: which rule decided that a listing is a repost.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-09
"""

import hashlib
from collections.abc import Sequence
from urllib.parse import urlparse

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _key(url: str) -> str:
    # Same rule as app.media.keys.image_key (the migration must not import application code).
    return hashlib.sha256((urlparse(url).path or url).encode("utf-8")).hexdigest()[:16]


def upgrade() -> None:
    op.add_column("listing_images", sa.Column("image_key", sa.String(16), nullable=True))
    op.add_column(
        "listing_images",
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column(
        "listing_images",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column("listing_images", sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("listing_images", sa.Column("source", sa.String(16), nullable=True))
    op.add_column("listings", sa.Column("duplicate_evidence", postgresql.JSONB(), nullable=True))

    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE listing_images i
               SET first_seen_at = coalesce(i.archived_at, l.first_seen_at),
                   last_seen_at = l.last_seen_at
              FROM listings l WHERE l.id = i.listing_id
            """
        )
    )
    rows = conn.execute(
        sa.text("SELECT id, listing_id, url FROM listing_images ORDER BY listing_id, position, id")
    ).all()
    seen: set[tuple[object, str]] = set()
    keyed: list[dict[str, object]] = []
    dupes: list[int] = []
    for r in rows:
        key = _key(r.url)
        keyed.append({"i": r.id, "k": key})
        if (r.listing_id, key) in seen:
            dupes.append(r.id)
        seen.add((r.listing_id, key))
    for start in range(0, len(keyed), 2000):
        conn.execute(
            sa.text("UPDATE listing_images SET image_key = :k WHERE id = :i"), keyed[start : start + 2000]
        )
    if dupes:
        conn.execute(
            sa.text("UPDATE listing_images SET removed_at = now() WHERE id = ANY(:ids)"), {"ids": dupes}
        )
    op.alter_column("listing_images", "image_key", nullable=False)

    op.drop_constraint("uq_listing_images_listing_id_position", "listing_images", type_="unique")
    op.create_index("ix_listing_images_listing_position", "listing_images", ["listing_id", "position"])
    op.create_index(
        "uq_listing_images_current_key",
        "listing_images",
        ["listing_id", "image_key"],
        unique=True,
        postgresql_where=sa.text("removed_at IS NULL"),
    )


def downgrade() -> None:
    # Retired photos have no place in the old shape (one row per position): they are dropped.
    op.execute("DELETE FROM listing_images WHERE removed_at IS NOT NULL")
    op.drop_index("uq_listing_images_current_key", table_name="listing_images")
    op.drop_index("ix_listing_images_listing_position", table_name="listing_images")
    op.execute(
        """
        UPDATE listing_images i SET position = r.rn - 1
          FROM (SELECT id, row_number() OVER (PARTITION BY listing_id ORDER BY position, id) AS rn
                  FROM listing_images) r
         WHERE r.id = i.id
        """
    )
    op.create_unique_constraint(
        "uq_listing_images_listing_id_position", "listing_images", ["listing_id", "position"]
    )
    op.drop_column("listings", "duplicate_evidence")
    for col in ("source", "removed_at", "last_seen_at", "first_seen_at", "image_key"):
        op.drop_column("listing_images", col)
