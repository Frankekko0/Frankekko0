"""phase 2.2: typed observations, sparing history, price history as a view

* ``listing_snapshots`` gains the reason for the row, a typed payload (observed / declared /
  inferred), the ordered photo keys, and the extension and parser versions. A listing seen again
  unchanged no longer adds a row (see ``app.ingestion.observations``).
* ``listings.last_verified_at``: last time a capture read the state of the listing.
* ``listing_price_history`` was a second copy of the prices in the snapshots; it becomes a view
  over them. Rows of the old table that no snapshot carries are first written as snapshots, so
  no price point is lost. (Pre-migration first-price rows were dated at the assumed publication;
  the snapshot that carries the same price keeps its real observation time.)

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
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


def upgrade() -> None:
    op.add_column("listing_snapshots", sa.Column("reason", sa.String(40), nullable=True))
    op.add_column("listing_snapshots", sa.Column("payload", postgresql.JSONB(), nullable=True))
    op.add_column("listing_snapshots", sa.Column("image_set", postgresql.JSONB(), nullable=True))
    op.add_column(
        "listing_snapshots",
        sa.Column("image_set_complete", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column("listing_snapshots", sa.Column("extension_version", sa.String(20), nullable=True))
    op.add_column("listing_snapshots", sa.Column("parser_version", sa.String(24), nullable=True))
    op.add_column("listings", sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        """
        UPDATE listings SET last_verified_at = coalesce(last_checked_at, last_seen_at)
         WHERE capture_level <> 'link' AND status <> 'unknown'
        """
    )
    # Price points no snapshot carries: keep them as snapshots, marked as migrated.
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


def downgrade() -> None:
    op.execute("DROP VIEW listing_price_history")
    op.create_table(
        "listing_price_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), server_default="EUR", nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listings.id"],
            name="fk_listing_price_history_listing_id_listings",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_listing_price_history"),
    )
    op.create_index(
        "ix_price_history_listing_observed", "listing_price_history", ["listing_id", "observed_at"]
    )
    op.execute(
        """
        INSERT INTO listing_price_history (listing_id, price, currency, observed_at)
        SELECT listing_id, price, currency, observed_at FROM (
            SELECT listing_id, price, currency, observed_at,
                   lag(price) OVER (PARTITION BY listing_id ORDER BY observed_at, id) AS previous_price
              FROM listing_snapshots WHERE price IS NOT NULL
        ) t WHERE previous_price IS DISTINCT FROM price
        """
    )
    op.drop_column("listings", "last_verified_at")
    for col in (
        "parser_version",
        "extension_version",
        "image_set_complete",
        "image_set",
        "payload",
        "reason",
    ):
        op.drop_column("listing_snapshots", col)
