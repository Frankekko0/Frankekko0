"""images: local archive copy of listing photos

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = (
    ("local_path", sa.String(200)),
    ("sha256", sa.String(64)),
    ("content_type", sa.String(40)),
    ("byte_size", sa.Integer()),
    ("archive_status", sa.String(16)),
    ("archive_error", sa.String(200)),
    ("archived_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    with op.batch_alter_table("listing_images") as b:
        for name, type_ in COLUMNS:
            b.add_column(sa.Column(name, type_, nullable=True))
        b.add_column(sa.Column("archive_attempts", sa.SmallInteger(), server_default="0", nullable=False))
    op.create_index(
        "ix_listing_images_archive_pending",
        "listing_images",
        ["listing_id"],
        postgresql_where=sa.text("archive_status IS NULL OR archive_status = 'failed'"),
    )


def downgrade() -> None:
    op.drop_index("ix_listing_images_archive_pending", table_name="listing_images")
    with op.batch_alter_table("listing_images") as b:
        b.drop_column("archive_attempts")
        for name, _ in reversed(COLUMNS):
            b.drop_column(name)
