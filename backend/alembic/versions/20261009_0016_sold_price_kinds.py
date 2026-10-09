"""phase 2.7: asking and realized prices in separate columns, sale-date window

``sold_sales.price`` stays (the figure used as evidence, its meaning given by ``price_kind``) but
what it is now also lives in its own column, so an asking price can never be read as a price
really paid:

* ``asking_price``   - the last price asked while the listing was on sale (``last_seen``);
* ``realized_price`` - the price really paid or received (``paid`` / ``received``: the user's own
  purchases and resales).

A sale seen on Vinted has an uncertain date: between the last time the listing was seen active
(``window_start``) and the moment the sale was noticed (``window_end``); ``sold_at`` is the
midpoint. ``observed_days`` is the time from the first sighting to the sale - a lower bound of
the days online, shown as such when the publication date is unknown.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)
MONEY = sa.Numeric(12, 2)


def upgrade() -> None:
    op.add_column("sold_sales", sa.Column("asking_price", MONEY, nullable=True))
    op.add_column("sold_sales", sa.Column("realized_price", MONEY, nullable=True))
    op.add_column("sold_sales", sa.Column("window_start", TS, nullable=True))
    op.add_column("sold_sales", sa.Column("window_end", TS, nullable=True))
    op.add_column("sold_sales", sa.Column("observed_days", sa.Numeric(7, 1), nullable=True))
    op.execute("UPDATE sold_sales SET asking_price = price WHERE price_kind = 'last_seen'")
    op.execute("UPDATE sold_sales SET realized_price = price WHERE price_kind IN ('paid', 'received')")
    op.execute(
        """
        UPDATE sold_sales s
           SET window_start = l.last_active_at,
               window_end = coalesce(l.sold_detected_at, s.sold_at),
               observed_days = CASE WHEN l.first_seen_at <= s.sold_at
                                    THEN round((extract(epoch FROM (s.sold_at - l.first_seen_at)) / 86400)::numeric, 1) END
          FROM listings l
         WHERE s.listing_id = l.id AND s.source = 'vinted_sold'
        """
    )
    op.create_check_constraint(
        "price_kind_columns",
        "sold_sales",
        "(price_kind = 'last_seen' AND asking_price IS NOT NULL AND realized_price IS NULL)"
        " OR (price_kind IN ('paid', 'received') AND realized_price IS NOT NULL AND asking_price IS NULL)"
        " OR (price_kind = 'reported' AND asking_price IS NULL AND realized_price IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("price_kind_columns", "sold_sales", type_="check")
    for col in ("observed_days", "window_end", "window_start", "realized_price", "asking_price"):
        op.drop_column("sold_sales", col)
