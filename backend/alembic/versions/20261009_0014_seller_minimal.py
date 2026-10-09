"""phase 2.5: sellers reduced to rating and review count, keys protected by the server

* Keys become ``s2:`` + HMAC of the old key with the server secret (``SELLER_KEY_SECRET``, else one
  derived from ``JWT_SECRET``), in place: listings keep pointing at the same seller row, so
  reposts, recycled photos and price habits keep working. The database alone can no longer be
  turned back into Vinted member ids by trying them all.
* ``account_created_at``, ``item_count``, ``sold_count``, ``country`` and ``last_active_at`` were
  never filled with real data and are not part of what FlipFinder keeps about a seller.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DROPPED = (
    ("account_created_at", sa.DateTime(timezone=True)),
    ("item_count", sa.Integer()),
    ("sold_count", sa.Integer()),
    ("country", sa.String(2)),
    ("last_active_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    from app.core.config import get_settings
    from app.core.seller_key import protect

    secret = get_settings().seller_key_secret_bytes()
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, external_id FROM sellers")).all()
    updates = [{"i": r.id, "k": protect(r.external_id, secret)} for r in rows]
    for start in range(0, len(updates), 1000):
        conn.execute(
            sa.text("UPDATE sellers SET external_id = :k WHERE id = :i"), updates[start : start + 1000]
        )
    for name, _ in DROPPED:
        op.drop_column("sellers", name)


def downgrade() -> None:
    # The columns come back empty (they never held real data). Keys stay protected: an HMAC
    # cannot be turned back, and nothing needs the old form.
    for name, type_ in DROPPED:
        op.add_column("sellers", sa.Column(name, type_, nullable=True))
