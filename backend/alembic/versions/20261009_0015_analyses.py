"""phase 2.6: permanent, immutable analyses in five blocks

``analyses`` keeps every analysis that produced a different result: traceability fields (listing,
Vinted id, URL, source, time, schema and algorithm versions), why it ran, a hash of its inputs and
of its results, and the blocks product / visual / economic / market / decision. Rows cannot be
updated. ``opportunities.analysis_id`` points at the current one, so the feed queries are unchanged.

Existing opportunities each get a first record (trigger ``migrated``) rebuilt from the data they
already hold; their columns and the score history are untouched.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "analyses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=False),
        sa.Column("vinted_id", sa.String(64), nullable=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("source", sa.String(24), nullable=True),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), nullable=False),
        sa.Column("algorithm_version", sa.String(32), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=False),
        sa.Column("inputs", postgresql.JSONB(), nullable=False),
        sa.Column("product", postgresql.JSONB(), nullable=False),
        sa.Column("visual", postgresql.JSONB(), nullable=False),
        sa.Column("economic", postgresql.JSONB(), nullable=False),
        sa.Column("market", postgresql.JSONB(), nullable=False),
        sa.Column("decision", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("btrim(url) <> ''", name=op.f("ck_analyses_url_not_empty")),
        sa.CheckConstraint(
            "trigger IN ('new', 'price_change', 'photos', 'status_change', 'data_changed', 'recompute',"
            " 'manual', 'migrated')",
            name=op.f("ck_analyses_trigger_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"], ["listings.id"], name=op.f("fk_analyses_listing_id_listings"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analyses")),
    )
    op.create_index("ix_analyses_listing_created", "analyses", ["listing_id", "created_at"])
    op.create_index(op.f("ix_analyses_vinted_id"), "analyses", ["vinted_id"])
    op.execute(
        """
        CREATE FUNCTION analyses_are_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'analyses are immutable: add a new analysis instead of changing %', OLD.id;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER analyses_no_update BEFORE UPDATE ON analyses"
        " FOR EACH ROW EXECUTE FUNCTION analyses_are_immutable()"
    )
    op.add_column("opportunities", sa.Column("analysis_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_opportunities_analysis_id_analyses"),
        "opportunities",
        "analyses",
        ["analysis_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_opportunities_analysis_id"), "opportunities", ["analysis_id"])

    # A first record for every existing opportunity, rebuilt from what it already holds.
    op.execute(
        """
        INSERT INTO analyses (id, listing_id, vinted_id, provider, url, source, created_at, schema_version,
                              algorithm_version, trigger, input_hash, result_hash, inputs,
                              product, visual, economic, market, decision)
        SELECT gen_random_uuid(), o.listing_id,
               CASE WHEN l.provider = 'vinted' THEN l.external_id END, l.provider, l.url,
               coalesce(o.acquisition_mode, l.acquisition_mode), o.analyzed_at, 1, o.algorithm_version,
               'migrated', 'migrated:' || o.id::text, 'migrated:' || o.id::text,
               jsonb_build_object('migrated', true),
               jsonb_build_object('v', 1, 'title', l.title, 'brand', l.brand_raw, 'brand_id', l.brand_id,
                   'category', l.category_raw, 'category_id', l.category_id, 'model', l.model_name,
                   'gender', l.gender, 'size', jsonb_build_object('raw', l.size_raw, 'normalized', l.size_normalized),
                   'condition', jsonb_build_object('raw', l.condition_raw, 'normalized', l.condition),
                   'color', l.color, 'material', l.material, 'is_vintage', l.is_vintage,
                   'product_code', l.product_code, 'price', o.listing_price, 'currency', o.currency,
                   'shipping_fee', l.shipping_fee, 'buyer_protection_fee', l.buyer_protection_fee,
                   'identification_confidence', o.identification_confidence,
                   'identification', coalesce(l.identification - 'vision', '{}'::jsonb),
                   'capture_level', l.capture_level),
               jsonb_build_object('v', 1, 'analysed', l.identification ? 'vision',
                   'vision', l.identification -> 'vision', 'photo_count', l.photo_count),
               jsonb_build_object('v', 1, 'listing_price', o.listing_price, 'currency', o.currency,
                   'total_acquisition_cost', o.total_acquisition_cost,
                   'expected_net_revenue', o.expected_net_revenue, 'max_buy_price', o.max_buy_price,
                   'good_buy_price', o.good_buy_price, 'suggested_offer', o.suggested_offer,
                   'risk_adjusted_profit', o.risk_adjusted_profit,
                   'scenarios', jsonb_build_object(
                       'conservative', jsonb_build_object('profit', o.conservative_profit, 'roi', o.conservative_roi),
                       'expected', jsonb_build_object('profit', o.expected_profit, 'roi', o.expected_roi),
                       'optimistic', jsonb_build_object('profit', o.optimistic_profit, 'roi', o.optimistic_roi))),
               jsonb_build_object('v', 1, 'data_quality', o.data_quality,
                   'insufficient_reason', o.insufficient_reason, 'fair_market_value', o.fair_market_value,
                   'market_median', o.market_median, 'market_mean', o.market_mean, 'market_p25', o.market_p25,
                   'market_p75', o.market_p75, 'market_min', o.market_min, 'market_max', o.market_max,
                   'quick_sale_price', o.quick_sale_price, 'expected_sale_price', o.expected_sale_price,
                   'optimistic_sale_price', o.optimistic_sale_price,
                   'discount_vs_market', o.discount_vs_market, 'comparables_count', o.comparables_count,
                   'sold_comparables_count', o.sold_comparables_count, 'demand_level', o.demand_level,
                   'demand_score', o.demand_score, 'sell_through_rate', o.sell_through_rate,
                   'velocity_score', o.velocity_score, 'estimated_days_to_sell', o.estimated_days_to_sell,
                   'snapshot', o.market_snapshot, 'provenance', o.score_breakdown -> 'provenance'),
               jsonb_build_object('v', 1, 'flip_score', o.flip_score, 'confidence_score', o.confidence_score,
                   'risk_score', o.risk_score, 'risk_level', o.risk_level, 'seller_score', o.seller_score,
                   'deal_tier', o.deal_tier, 'is_ultra_deal', o.is_ultra_deal, 'verdict', o.verdict,
                   'recommended_action', o.recommended_action, 'headline', o.headline,
                   'sale_probability', o.sale_probability,
                   'authenticity_probability', o.authenticity_probability,
                   'authenticity_verdict', o.authenticity_verdict, 'explanation', o.explanation,
                   'risk_factors', o.risk_factors, 'components', o.score_breakdown -> 'components',
                   'penalties', o.score_breakdown -> 'penalties', 'cap', o.score_breakdown -> 'cap',
                   'base', o.score_breakdown -> 'base')
          FROM opportunities o JOIN listings l ON l.id = o.listing_id
        """
    )
    op.execute(
        "UPDATE opportunities o SET analysis_id = a.id FROM analyses a"
        " WHERE a.listing_id = o.listing_id AND a.trigger = 'migrated'"
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_opportunities_analysis_id"), table_name="opportunities")
    op.drop_constraint(op.f("fk_opportunities_analysis_id_analyses"), "opportunities", type_="foreignkey")
    op.drop_column("opportunities", "analysis_id")
    op.execute("DROP TRIGGER analyses_no_update ON analyses")
    op.execute("DROP FUNCTION analyses_are_immutable()")
    op.drop_index(op.f("ix_analyses_vinted_id"), table_name="analyses")
    op.drop_index("ix_analyses_listing_created", table_name="analyses")
    op.drop_table("analyses")
