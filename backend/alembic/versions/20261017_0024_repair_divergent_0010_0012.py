"""Repair for databases that reached 0012 through another line of development

Two lines of development used the revision numbers 0010-0012 for different changes. A database made by the other
line says "0012" but never received this line's 0010-0012 (publication-date provenance, typed observations,
photos kept over time), and then 0013-0023 were applied on top. The symptom is a missing column such as
``listing_images.image_key``.

This revision adds, only where it is missing, what this line's 0010-0012 would have created. Every step first looks
at the database: a database that followed this line has nothing missing and nothing changes. Nothing is deleted;
the tables and columns of the other line are left where they are. Not reversible on purpose (it repairs).

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-17
"""

import hashlib
from collections.abc import Sequence
from urllib.parse import urlparse

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VIEW = """
CREATE VIEW listing_price_history AS
SELECT id, listing_id, price, currency, observed_at
  FROM (
        SELECT id, listing_id, price, currency, observed_at,
               lag(price) OVER (PARTITION BY listing_id ORDER BY observed_at, id) AS previous_price
          FROM listing_snapshots
         WHERE price IS NOT NULL
       ) priced
 WHERE previous_price IS DISTINCT FROM price
"""


def _cols(conn: sa.Connection, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(conn).get_columns(table)}


def _exists(conn: sa.Connection, sql: str, **params: object) -> bool:
    return conn.execute(sa.text(sql), params).first() is not None


def _constraint(conn: sa.Connection, name: str) -> bool:
    return _exists(conn, "SELECT 1 FROM pg_constraint WHERE conname = :n", n=name)


def _index(conn: sa.Connection, name: str) -> bool:
    return _exists(conn, "SELECT 1 FROM pg_indexes WHERE indexname = :n", n=name)


def _key(url: str) -> str:
    # Same rule as app.media.keys.image_key (a migration must not import application code).
    return hashlib.sha256((urlparse(url).path or url).encode("utf-8")).hexdigest()[:16]


def upgrade() -> None:
    conn = op.get_bind()

    # ---- 0010: provenance of the publication date, non-empty URL --------------------------------------------
    if "published_at_kind" not in _cols(conn, "listings"):
        op.add_column(
            "listings", sa.Column("published_at_kind", sa.String(8), server_default="unknown", nullable=False)
        )
        op.execute("UPDATE listings SET published_at_kind = 'reported' WHERE published_at IS NOT NULL")
        op.execute(
            """
            UPDATE listings
               SET published_at = NULL, published_at_kind = 'unknown', days_to_sell = NULL
             WHERE published_at IS NOT NULL
               AND abs(extract(epoch FROM (published_at - first_seen_at))) < 1
            """
        )
    if not _constraint(conn, "ck_listings_published_at_kind_valid") and not _constraint(
        conn, "published_at_kind_valid"
    ):
        op.create_check_constraint(
            "published_at_kind_valid",
            "listings",
            "published_at_kind IN ('exact', 'relative', 'reported', 'unknown')",
        )
    empty_urls = conn.execute(sa.text("SELECT count(*) FROM listings WHERE btrim(url) = ''")).scalar_one()
    if (
        not empty_urls
        and not _constraint(conn, "ck_listings_url_not_empty")
        and not _constraint(conn, "url_not_empty")
    ):
        op.create_check_constraint("url_not_empty", "listings", "btrim(url) <> ''")

    # ---- 0011: typed observations, last verification, price history as a view -------------------------------
    snap = _cols(conn, "listing_snapshots")
    for name, column in (
        ("reason", sa.Column("reason", sa.String(40), nullable=True)),
        ("payload", sa.Column("payload", postgresql.JSONB(), nullable=True)),
        ("image_set", sa.Column("image_set", postgresql.JSONB(), nullable=True)),
        (
            "image_set_complete",
            sa.Column("image_set_complete", sa.Boolean(), server_default="false", nullable=False),
        ),
        ("extension_version", sa.Column("extension_version", sa.String(20), nullable=True)),
        ("parser_version", sa.Column("parser_version", sa.String(24), nullable=True)),
    ):
        if name not in snap:
            op.add_column("listing_snapshots", column)
    if "last_verified_at" not in _cols(conn, "listings"):
        op.add_column("listings", sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True))
        op.execute(
            """
            UPDATE listings SET last_verified_at = coalesce(last_checked_at, last_seen_at)
             WHERE capture_level <> 'link' AND status <> 'unknown'
            """
        )
    kind = conn.execute(
        sa.text(
            "SELECT relkind FROM pg_class WHERE relname = 'listing_price_history' AND relnamespace = 'public'::regnamespace"
        )
    ).scalar()
    if kind == "r":  # the old table: its price points that no snapshot carries are kept as migrated snapshots
        op.execute(
            """
            INSERT INTO listing_snapshots (listing_id, observed_at, acquisition_mode, capture_level, status,
                                           price, currency, note, reason)
            SELECT h.listing_id, h.observed_at, l.acquisition_mode, NULL, NULL, h.price, h.currency,
                   'Prezzo storico migrato dalla vecchia tabella.', 'migrated'
              FROM listing_price_history h
              JOIN listings l ON l.id = h.listing_id
             WHERE NOT EXISTS (
                   SELECT 1 FROM listing_snapshots s WHERE s.listing_id = h.listing_id AND s.price = h.price)
            """
        )
        op.drop_table("listing_price_history")
        op.execute(VIEW)
    elif kind is None:
        op.execute(VIEW)

    # ---- 0012: photos kept over time, repost evidence --------------------------------------------------------
    if "duplicate_evidence" not in _cols(conn, "listings"):
        op.add_column("listings", sa.Column("duplicate_evidence", postgresql.JSONB(), nullable=True))
    images = _cols(conn, "listing_images")
    if "image_key" not in images:
        for name, column in (
            ("image_key", sa.Column("image_key", sa.String(16), nullable=True)),
            (
                "first_seen_at",
                sa.Column(
                    "first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
                ),
            ),
            (
                "last_seen_at",
                sa.Column(
                    "last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
                ),
            ),
            ("removed_at", sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True)),
            ("source", sa.Column("source", sa.String(16), nullable=True)),
        ):
            if name not in images:
                op.add_column("listing_images", column)
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
        op.execute(
            "ALTER TABLE listing_images DROP CONSTRAINT IF EXISTS uq_listing_images_listing_id_position"
        )
    if not _index(conn, "ix_listing_images_listing_position"):
        op.create_index("ix_listing_images_listing_position", "listing_images", ["listing_id", "position"])
    if not _index(conn, "uq_listing_images_current_key"):
        op.create_index(
            "uq_listing_images_current_key",
            "listing_images",
            ["listing_id", "image_key"],
            unique=True,
            postgresql_where=sa.text("removed_at IS NULL"),
        )


def downgrade() -> None:
    """A repair is not undone: the columns it added are what the application reads."""
