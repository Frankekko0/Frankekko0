"""purge the generated demo market and the demo account

Everything the old demo mode created goes: listings and sellers of the simulated market (with
their photos, snapshots, price history, analyses, comparables, favourites, alerts and logs),
the demo account, products only those listings referred to, and every figure learned from the
simulated sales (market statistics, price calibration, preference learning, scanner state).
They are rebuilt from real data by the worker. Your own records (purchases, sales, captures)
are kept. Take a backup first (`make backup` / scripts/backup.sh): this cannot be undone.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM alerts WHERE listing_id IN (SELECT id FROM listings WHERE provider = 'mock') OR opportunity_id IN (SELECT id FROM opportunities WHERE listing_id IN (SELECT id FROM listings WHERE provider = 'mock'))"
    )
    op.execute(
        "DELETE FROM acquisition_attempts WHERE listing_id IN (SELECT id FROM listings WHERE provider = 'mock')"
    )
    op.execute(
        "UPDATE listings SET duplicate_of_id = NULL WHERE duplicate_of_id IN (SELECT id FROM listings WHERE provider = 'mock') AND provider <> 'mock'"
    )
    op.execute(
        "DELETE FROM listings WHERE provider = 'mock'"
    )  # cascades: images, snapshots, history, opportunities...
    op.execute("DELETE FROM sellers WHERE provider = 'mock'")
    op.execute(
        "DELETE FROM products p WHERE NOT EXISTS (SELECT 1 FROM listings l WHERE l.product_id = p.id) AND NOT EXISTS (SELECT 1 FROM opportunities o WHERE o.product_id = p.id)"
    )
    op.execute(
        "DELETE FROM users WHERE email = 'demo@flipfinder.app'"
    )  # cascades: watchlists, preferences, keys...
    op.execute("DELETE FROM market_statistics")
    op.execute("DELETE FROM user_affinities")
    op.execute(
        "DELETE FROM system_state WHERE key IN ('mock_market_epoch', 'price_calibration', 'scanner_last_run') "
        "OR key LIKE 'scanner_cursor:%'"
    )


def downgrade() -> None:
    # Deleted demo data is not recreated (restore the backup if needed).
    pass
