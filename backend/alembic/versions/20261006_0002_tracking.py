"""tracking: snapshots, acquisition log, lifecycle fields, single Vinted provider, data minimization

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-06

Data migration (no listing, price or analysis is lost):

* every past price (``listing_price_history``) becomes a snapshot, plus one snapshot with the
  state of each listing at migration time;
* ``possibly_sold`` (a sale inferred from a disappearance) becomes ``removed``: sales are now
  recorded only on positive evidence;
* manual/extension imports of Vinted links move to the single ``vinted`` provider, so the same
  item is one record whatever the acquisition mode (dedup by Vinted ID);
* sellers keep only rating and review count: usernames are dropped, external ids of real sellers
  are replaced by a one-way hash (still groups a seller's listings, reveals nothing).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VINTED_ITEM_URL = (
    r"^https?://(www\.)?vinted\.(it|fr|de|es|be|nl|lu|pt|at|pl|cz|sk|lt|co\.uk|com|se|fi|dk|gr|hr|ro|hu|ie|si|lv|ee)"
    r"(:[0-9]+)?/items/[0-9]+"
)


def upgrade() -> None:
    # ------------------------------------------------------------------ listings: new columns
    with op.batch_alter_table("listings") as b:
        b.add_column(sa.Column("sold_detected_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("last_active_price", sa.Numeric(12, 2), nullable=True))
        b.add_column(sa.Column("days_to_sell", sa.Numeric(7, 1), nullable=True))
        b.add_column(
            sa.Column("acquisition_mode", sa.String(24), server_default="provider_scan", nullable=False)
        )
        b.add_column(sa.Column("capture_level", sa.String(8), server_default="full", nullable=False))
        b.add_column(sa.Column("tracked_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("check_failures", sa.SmallInteger(), server_default="0", nullable=False))
        b.add_column(sa.Column("unchanged_checks", sa.SmallInteger(), server_default="0", nullable=False))
    op.create_index("ix_listings_next_check_at", "listings", ["next_check_at"])
    op.create_index(
        "ix_listings_tracked_at", "listings", ["tracked_at"], postgresql_where=sa.text("tracked_at IS NOT NULL")
    )

    # ------------------------------------------------------------------ new tables
    op.create_table(
        "listing_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("listing_id", sa.UUID(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("acquisition_mode", sa.String(24), nullable=False),
        sa.Column("capture_level", sa.String(8), nullable=True),
        sa.Column("status", sa.String(16), nullable=True),
        sa.Column("price", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(3), server_default="EUR", nullable=False),
        sa.Column("favourite_count", sa.Integer(), nullable=True),
        sa.Column("view_count", sa.Integer(), nullable=True),
        sa.Column("photo_count", sa.SmallInteger(), nullable=True),
        sa.Column("note", sa.String(200), nullable=True),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listings.id"],
            name=op.f("fk_listing_snapshots_listing_id_listings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_listing_snapshots")),
    )
    op.create_index(
        "ix_listing_snapshots_listing_observed", "listing_snapshots", ["listing_id", "observed_at"]
    )
    op.create_table(
        "acquisition_attempts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("listing_id", sa.UUID(), nullable=True),
        sa.Column("vinted_id", sa.String(64), nullable=True),
        sa.Column("mode", sa.String(24), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("http_status", sa.SmallInteger(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listings.id"],
            name=op.f("fk_acquisition_attempts_listing_id_listings"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_acquisition_attempts")),
    )
    op.create_index("ix_acquisition_attempts_started", "acquisition_attempts", ["started_at"])
    op.create_index("ix_acquisition_attempts_listing", "acquisition_attempts", ["listing_id", "started_at"])

    # ------------------------------------------------------------------ analyses: traceability
    with op.batch_alter_table("opportunities") as b:
        b.add_column(sa.Column("acquisition_mode", sa.String(24), nullable=True))
        b.add_column(sa.Column("analysis_depth", sa.String(8), server_default="full", nullable=False))
        b.add_column(sa.Column("data_quality", sa.String(16), server_default="ok", nullable=False))
        b.add_column(sa.Column("insufficient_reason", sa.String(300), nullable=True))
    with op.batch_alter_table("opportunity_scores") as b:
        b.add_column(sa.Column("acquisition_mode", sa.String(24), nullable=True))
        b.add_column(sa.Column("analysis_depth", sa.String(8), nullable=True))
        b.add_column(sa.Column("data_quality", sa.String(16), nullable=True))

    # ------------------------------------------------------------------ data migration
    # Sales only on evidence: a disappearance is a removal.
    op.execute(
        """
        UPDATE listings
           SET status = 'removed',
               removed_at = COALESCE(removed_at, status_changed_at, last_seen_at)
         WHERE status = 'possibly_sold'
        """
    )
    # One provider for real Vinted items, whatever the acquisition mode.
    op.execute(
        f"""
        UPDATE listings
           SET provider = 'vinted'
         WHERE provider = 'manual'
           AND external_id ~ '^[0-9]+$'
           AND url ~* '{VINTED_ITEM_URL}'
        """
    )
    op.execute(
        """
        UPDATE listings
           SET acquisition_mode = CASE
                   WHEN provider = 'mock' THEN 'provider_scan'
                   WHEN raw ->> 'source' = 'vinted_search_import' THEN 'batch_import'
                   WHEN raw ->> 'source' = 'manual_import' THEN 'manual_form'
                   WHEN provider IN ('vinted', 'manual') THEN 'migrated'
                   ELSE 'provider_scan'
               END,
               capture_level = CASE
                   WHEN raw ->> 'source' = 'vinted_search_import' THEN 'card'
                   ELSE 'full'
               END,
               tracked_at = CASE WHEN provider IN ('vinted', 'manual') THEN first_seen_at END,
               last_checked_at = last_seen_at,
               last_active_at = CASE WHEN status IN ('active', 'reserved') THEN last_seen_at END,
               last_active_price = price,
               sold_detected_at = CASE WHEN status = 'sold' THEN sold_at END,
               days_to_sell = CASE
                   WHEN status = 'sold' AND sold_at IS NOT NULL AND published_at IS NOT NULL
                        AND sold_at >= published_at
                   THEN round((extract(epoch FROM sold_at - published_at) / 86400)::numeric, 1)
               END,
               next_check_at = CASE WHEN status IN ('active', 'reserved', 'unknown') THEN now() END
        """
    )
    # Price history -> snapshots, plus the state of every listing now.
    op.execute(
        """
        INSERT INTO listing_snapshots
            (listing_id, observed_at, acquisition_mode, capture_level, status, price, currency, note)
        SELECT h.listing_id, h.observed_at, 'migrated', NULL, NULL, h.price, h.currency,
               'Prezzo dallo storico precedente'
          FROM listing_price_history h
        """
    )
    op.execute(
        """
        INSERT INTO listing_snapshots
            (listing_id, observed_at, acquisition_mode, capture_level, status, price, currency,
             favourite_count, view_count, photo_count, note)
        SELECT id, last_seen_at, 'migrated', capture_level, status, price, currency,
               favourite_count, view_count, photo_count, 'Stato al momento della migrazione'
          FROM listings
        """
    )
    op.execute(
        """
        UPDATE opportunities o
           SET acquisition_mode = l.acquisition_mode,
               analysis_depth = CASE WHEN l.capture_level = 'card' THEN 'quick' ELSE 'full' END
          FROM listings l
         WHERE l.id = o.listing_id
        """
    )
    # Sellers: only rating and review count. Real sellers' ids become one-way hashes.
    op.execute("UPDATE sellers SET provider = 'vinted' WHERE provider = 'manual'")
    op.execute(
        """
        UPDATE sellers
           SET external_id = 'h:' || left(encode(sha256(convert_to(external_id, 'UTF8')), 'hex'), 24),
               account_created_at = NULL,
               item_count = NULL,
               sold_count = NULL,
               country = NULL,
               last_active_at = NULL
         WHERE provider <> 'mock' AND external_id NOT LIKE 'h:%'
        """
    )
    with op.batch_alter_table("sellers") as b:
        b.drop_column("username")


def downgrade() -> None:
    with op.batch_alter_table("sellers") as b:
        b.add_column(sa.Column("username", sa.String(120), nullable=True))
    with op.batch_alter_table("opportunity_scores") as b:
        b.drop_column("data_quality")
        b.drop_column("analysis_depth")
        b.drop_column("acquisition_mode")
    with op.batch_alter_table("opportunities") as b:
        b.drop_column("insufficient_reason")
        b.drop_column("data_quality")
        b.drop_column("analysis_depth")
        b.drop_column("acquisition_mode")
    op.drop_index("ix_acquisition_attempts_listing", table_name="acquisition_attempts")
    op.drop_index("ix_acquisition_attempts_started", table_name="acquisition_attempts")
    op.drop_table("acquisition_attempts")
    op.drop_index("ix_listing_snapshots_listing_observed", table_name="listing_snapshots")
    op.drop_table("listing_snapshots")
    op.drop_index("ix_listings_tracked_at", table_name="listings")
    op.drop_index("ix_listings_next_check_at", table_name="listings")
    with op.batch_alter_table("listings") as b:
        for col in (
            "unchanged_checks",
            "check_failures",
            "next_check_at",
            "last_checked_at",
            "tracked_at",
            "capture_level",
            "acquisition_mode",
            "days_to_sell",
            "last_active_price",
            "last_active_at",
            "sold_detected_at",
        ):
            b.drop_column(col)
    # Reclassified statuses (possibly_sold -> removed) and hashed seller ids are not restored.
