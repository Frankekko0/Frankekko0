"""price evidence: concluded sales, external prices, per-model search cache and statistics

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)
MONEY = sa.Numeric(12, 2)
RATIO = sa.Numeric(8, 4)


def _fk(table: str, col: str, ref: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([col], [f"{ref}.id"], name=f"fk_{table}_{col}_{ref}", ondelete=ondelete)


def upgrade() -> None:
    op.create_table(
        "external_prices",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("dedupe_key", sa.String(80), nullable=False),
        sa.Column("model_key", sa.String(160), nullable=False),
        sa.Column("brand_id", sa.Integer(), nullable=True),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("model_name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("price", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("price_eur", MONEY, nullable=False),
        sa.Column("condition", sa.String(24), server_default="unknown", nullable=False),
        sa.Column("size", sa.String(20), nullable=True),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("query", sa.String(300), nullable=False),
        sa.Column("observed_at", TS, nullable=False),
        sa.Column("source_date", TS, nullable=True),
        sa.Column("match_score", RATIO, nullable=False),
        sa.Column("match", postgresql.JSONB(), nullable=True),
        sa.Column("is_outlier", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("price > 0", name="ck_external_prices_price_positive"),
        sa.CheckConstraint("kind IN ('new', 'asking', 'sold')", name="ck_external_prices_kind_valid"),
        _fk("external_prices", "brand_id", "brands", "CASCADE"),
        _fk("external_prices", "category_id", "categories", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_external_prices"),
        sa.UniqueConstraint("dedupe_key", name="uq_external_prices_dedupe_key"),
    )
    op.create_index("ix_external_prices_model_kind", "external_prices", ["model_key", "kind"])
    op.create_index("ix_external_prices_brand_model", "external_prices", ["brand_id", "model_name"])

    op.create_table(
        "external_searches",
        sa.Column("model_key", sa.String(160), nullable=False),
        sa.Column("brand_id", sa.Integer(), nullable=True),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("model_name", sa.String(120), nullable=False),
        sa.Column("demand", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("last_searched_at", TS, nullable=True),
        sa.Column("next_refresh_at", TS, nullable=True),
        sa.Column("queries_used", sa.Integer(), server_default="0", nullable=False),
        sa.Column("results", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.String(300), nullable=True),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
        _fk("external_searches", "brand_id", "brands", "CASCADE"),
        _fk("external_searches", "category_id", "categories", "SET NULL"),
        sa.PrimaryKeyConstraint("model_key", name="pk_external_searches"),
    )
    op.create_index("ix_external_searches_next_refresh_at", "external_searches", ["next_refresh_at"])

    op.create_table(
        "sold_sales",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("dedupe_key", sa.String(80), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("reliability", sa.SmallInteger(), nullable=False),
        sa.Column("price_kind", sa.String(12), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("purchase_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sale_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("external_price_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("brand_id", sa.Integer(), nullable=True),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("model_name", sa.String(120), nullable=True),
        sa.Column("size_normalized", sa.String(20), nullable=True),
        sa.Column("condition", sa.String(24), server_default="unknown", nullable=False),
        sa.Column("price", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), server_default="EUR", nullable=False),
        sa.Column("price_eur", MONEY, nullable=False),
        sa.Column("sold_at", TS, nullable=False),
        sa.Column("published_at", TS, nullable=True),
        sa.Column("days_to_sell", sa.Numeric(7, 1), nullable=True),
        sa.Column("source_name", sa.String(80), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("is_outlier", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("price > 0", name="ck_sold_sales_price_positive"),
        _fk("sold_sales", "user_id", "users", "CASCADE"),
        _fk("sold_sales", "listing_id", "listings", "SET NULL"),
        _fk("sold_sales", "purchase_id", "purchases", "CASCADE"),
        _fk("sold_sales", "sale_id", "sales", "CASCADE"),
        _fk("sold_sales", "external_price_id", "external_prices", "CASCADE"),
        _fk("sold_sales", "brand_id", "brands", "SET NULL"),
        _fk("sold_sales", "category_id", "categories", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_sold_sales"),
        sa.UniqueConstraint("dedupe_key", name="uq_sold_sales_dedupe_key"),
    )
    op.create_index(
        "ix_sold_sales_segment",
        "sold_sales",
        ["brand_id", "category_id", "model_name", "size_normalized", "condition"],
    )
    op.create_index("ix_sold_sales_brand_model", "sold_sales", ["brand_id", "model_name"])
    op.create_index("ix_sold_sales_sold_at", "sold_sales", ["sold_at"])
    op.create_index("ix_sold_sales_source", "sold_sales", ["source"])
    op.create_index("ix_sold_sales_listing_id", "sold_sales", ["listing_id"])

    op.create_table(
        "model_price_stats",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("segment_key", sa.String(255), nullable=False),
        sa.Column("brand_id", sa.Integer(), nullable=True),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("model_name", sa.String(120), nullable=True),
        sa.Column("size_normalized", sa.String(20), nullable=True),
        sa.Column("condition", sa.String(24), nullable=True),
        sa.Column("price_basis", sa.String(8), nullable=False),
        sa.Column("median_price", MONEY, nullable=False),
        sa.Column("low_price", MONEY, nullable=False),
        sa.Column("high_price", MONEY, nullable=False),
        sa.Column("n_samples", sa.Integer(), nullable=False),
        sa.Column("n_sales", sa.Integer(), nullable=False),
        sa.Column("n_own", sa.Integer(), nullable=False),
        sa.Column("n_vinted_sold", sa.Integer(), nullable=False),
        sa.Column("n_external_sold", sa.Integer(), nullable=False),
        sa.Column("n_asking", sa.Integer(), nullable=False),
        sa.Column("n_outliers", sa.Integer(), nullable=False),
        sa.Column("avg_days_to_sell", sa.Numeric(7, 1), nullable=True),
        sa.Column("median_days_to_sell", sa.Numeric(7, 1), nullable=True),
        sa.Column("n_seen", sa.Integer(), nullable=False),
        sa.Column("sell_through", RATIO, nullable=True),
        sa.Column("median_new_price", MONEY, nullable=True),
        sa.Column("n_new", sa.Integer(), server_default="0", nullable=False),
        sa.Column("negotiation_discount", RATIO, nullable=True),
        sa.Column("sources", postgresql.JSONB(), nullable=True),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("computed_at", TS, server_default=sa.func.now(), nullable=False),
        _fk("model_price_stats", "brand_id", "brands", "CASCADE"),
        _fk("model_price_stats", "category_id", "categories", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_model_price_stats"),
        sa.UniqueConstraint("segment_key", name="uq_model_price_stats_segment_key"),
    )
    op.create_index(
        "ix_model_price_stats_lookup",
        "model_price_stats",
        ["brand_id", "model_name", "size_normalized", "condition"],
    )

    # Comparable lookups: sold listings of a model (sale date order) and per-model segments.
    op.create_index(
        "ix_listings_brand_model_status",
        "listings",
        ["brand_id", "model_name", "status"],
    )
    op.create_index(
        "ix_listings_sold_segment",
        "listings",
        ["brand_id", "category_id", "sold_at"],
        postgresql_where=sa.text("status = 'sold'"),
    )


def downgrade() -> None:
    op.drop_index("ix_listings_sold_segment", table_name="listings")
    op.drop_index("ix_listings_brand_model_status", table_name="listings")
    op.drop_index("ix_model_price_stats_lookup", table_name="model_price_stats")
    op.drop_table("model_price_stats")
    for ix in (
        "ix_sold_sales_listing_id",
        "ix_sold_sales_source",
        "ix_sold_sales_sold_at",
        "ix_sold_sales_brand_model",
        "ix_sold_sales_segment",
    ):
        op.drop_index(ix, table_name="sold_sales")
    op.drop_table("sold_sales")
    op.drop_index("ix_external_searches_next_refresh_at", table_name="external_searches")
    op.drop_table("external_searches")
    op.drop_index("ix_external_prices_brand_model", table_name="external_prices")
    op.drop_index("ix_external_prices_model_kind", table_name="external_prices")
    op.drop_table("external_prices")
