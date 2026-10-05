"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-10-05 13:43:56.597574
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Trigram index support for fuzzy title search.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table('brands',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('slug', sa.String(length=120), nullable=False),
    sa.Column('aliases', postgresql.ARRAY(sa.String(length=120)), server_default='{}', nullable=False),
    sa.Column('tier', sa.String(length=24), server_default='mid', nullable=False),
    sa.Column('counterfeit_risk', sa.Numeric(precision=8, scale=4), server_default='0.05', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('counterfeit_risk >= 0 AND counterfeit_risk <= 1', name=op.f('ck_brands_counterfeit_risk')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_brands')),
    sa.UniqueConstraint('slug', name=op.f('uq_brands_slug'))
    )
    op.create_table('categories',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('slug', sa.String(length=80), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('name_it', sa.String(length=120), nullable=False),
    sa.Column('parent_id', sa.Integer(), nullable=True),
    sa.Column('baseline_days_to_sell', sa.SmallInteger(), server_default='14', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['parent_id'], ['categories.id'], name=op.f('fk_categories_parent_id_categories'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_categories')),
    sa.UniqueConstraint('slug', name=op.f('uq_categories_slug'))
    )
    op.create_index(op.f('ix_categories_parent_id'), 'categories', ['parent_id'], unique=False)
    op.create_table('sellers',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('external_id', sa.String(length=64), nullable=False),
    sa.Column('username', sa.String(length=120), nullable=True),
    sa.Column('rating', sa.Numeric(precision=3, scale=2), nullable=True),
    sa.Column('review_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('account_created_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('item_count', sa.Integer(), nullable=True),
    sa.Column('sold_count', sa.Integer(), nullable=True),
    sa.Column('country', sa.String(length=2), nullable=True),
    sa.Column('last_active_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reliability_score', sa.SmallInteger(), nullable=True),
    sa.Column('reliability_details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sellers')),
    sa.UniqueConstraint('provider', 'external_id', name=op.f('uq_sellers_provider_external_id'))
    )
    op.create_table('system_state',
    sa.Column('key', sa.String(length=80), nullable=False),
    sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('key', name=op.f('pk_system_state'))
    )
    op.create_table('users',
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('display_name', sa.String(length=80), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('token_version', sa.Integer(), server_default='0', nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('email', name=op.f('uq_users_email'))
    )
    op.create_table('market_statistics',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('segment_key', sa.String(length=255), nullable=False),
    sa.Column('brand_id', sa.Integer(), nullable=True),
    sa.Column('category_id', sa.Integer(), nullable=True),
    sa.Column('model_name', sa.String(length=120), nullable=True),
    sa.Column('size_normalized', sa.String(length=20), nullable=True),
    sa.Column('sample_size', sa.Integer(), nullable=False),
    sa.Column('sold_count', sa.Integer(), nullable=False),
    sa.Column('active_count', sa.Integer(), nullable=False),
    sa.Column('median_price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('mean_price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('p25_price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('p75_price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('min_reasonable_price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('max_reasonable_price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('avg_listing_price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('median_sold_price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('ask_to_sale_ratio', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.Column('sell_through_rate', sa.Numeric(precision=8, scale=4), nullable=False),
    sa.Column('avg_days_to_sale', sa.Numeric(precision=6, scale=2), nullable=True),
    sa.Column('window_days', sa.Integer(), nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['brand_id'], ['brands.id'], name=op.f('fk_market_statistics_brand_id_brands'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['category_id'], ['categories.id'], name=op.f('fk_market_statistics_category_id_categories'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_market_statistics')),
    sa.UniqueConstraint('segment_key', name=op.f('uq_market_statistics_segment_key'))
    )
    op.create_index('ix_market_statistics_brand_category', 'market_statistics', ['brand_id', 'category_id'], unique=False)
    op.create_table('notification_settings',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('in_app_enabled', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('web_push_enabled', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('email_enabled', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('email_address', sa.String(length=320), nullable=True),
    sa.Column('telegram_enabled', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('telegram_chat_id', sa.String(length=64), nullable=True),
    sa.Column('discord_enabled', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('discord_webhook_url', sa.Text(), nullable=True),
    sa.Column('new_opportunity_alerts', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('ultra_deal_alerts', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('price_drop_alerts', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('watchlist_alerts', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('alert_min_flip_score', sa.SmallInteger(), server_default='85', nullable=False),
    sa.Column('alert_min_roi', sa.Numeric(precision=8, scale=4), server_default='0.50', nullable=False),
    sa.Column('alert_min_profit', sa.Numeric(precision=12, scale=2), server_default='15', nullable=False),
    sa.Column('alert_min_confidence', sa.SmallInteger(), server_default='70', nullable=False),
    sa.Column('alert_max_risk_score', sa.SmallInteger(), server_default='60', nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_notification_settings_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', name=op.f('pk_notification_settings'))
    )
    op.create_table('products',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('product_key', sa.String(length=255), nullable=False),
    sa.Column('brand_id', sa.Integer(), nullable=True),
    sa.Column('category_id', sa.Integer(), nullable=True),
    sa.Column('model_name', sa.String(length=120), nullable=True),
    sa.Column('gender', sa.String(length=10), nullable=True),
    sa.Column('canonical_name', sa.String(length=255), nullable=False),
    sa.Column('sku', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['brand_id'], ['brands.id'], name=op.f('fk_products_brand_id_brands'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['category_id'], ['categories.id'], name=op.f('fk_products_category_id_categories'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_products')),
    sa.UniqueConstraint('product_key', name=op.f('uq_products_product_key'))
    )
    op.create_index(op.f('ix_products_brand_id'), 'products', ['brand_id'], unique=False)
    op.create_index(op.f('ix_products_category_id'), 'products', ['category_id'], unique=False)
    op.create_table('push_subscriptions',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('endpoint', sa.Text(), nullable=False),
    sa.Column('p256dh', sa.String(length=255), nullable=False),
    sa.Column('auth', sa.String(length=255), nullable=False),
    sa.Column('user_agent', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_push_subscriptions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_push_subscriptions')),
    sa.UniqueConstraint('endpoint', name=op.f('uq_push_subscriptions_endpoint'))
    )
    op.create_index(op.f('ix_push_subscriptions_user_id'), 'push_subscriptions', ['user_id'], unique=False)
    op.create_table('user_affinities',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('dimension', sa.String(length=16), nullable=False),
    sa.Column('key', sa.String(length=120), nullable=False),
    sa.Column('flips_count', sa.Integer(), nullable=False),
    sa.Column('wins', sa.Integer(), nullable=False),
    sa.Column('avg_roi', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.Column('avg_profit', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('avg_holding_days', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.Column('ignored_count', sa.Integer(), nullable=False),
    sa.Column('saved_count', sa.Integer(), nullable=False),
    sa.Column('adjustment', sa.Numeric(precision=8, scale=4), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_affinities_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_user_affinities')),
    sa.UniqueConstraint('user_id', 'dimension', 'key', name=op.f('uq_user_affinities_user_id_dimension_key'))
    )
    op.create_index(op.f('ix_user_affinities_user_id'), 'user_affinities', ['user_id'], unique=False)
    op.create_table('user_preferences',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('locale', sa.String(length=8), server_default='it', nullable=False),
    sa.Column('currency', sa.String(length=3), server_default='EUR', nullable=False),
    sa.Column('preferred_brands', postgresql.ARRAY(sa.String(length=80)), server_default='{}', nullable=False),
    sa.Column('preferred_categories', postgresql.ARRAY(sa.String(length=80)), server_default='{}', nullable=False),
    sa.Column('sizes', postgresql.ARRAY(sa.String(length=20)), server_default='{}', nullable=False),
    sa.Column('min_profit', sa.Numeric(precision=12, scale=2), server_default='10', nullable=False),
    sa.Column('min_roi', sa.Numeric(precision=8, scale=4), server_default='0.40', nullable=False),
    sa.Column('max_purchase_price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('min_flip_score', sa.SmallInteger(), nullable=True),
    sa.Column('max_risk_score', sa.SmallInteger(), nullable=True),
    sa.Column('min_confidence', sa.SmallInteger(), nullable=True),
    sa.Column('cost_profile', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('score_weights', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('personalization_enabled', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_preferences_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', name=op.f('pk_user_preferences'))
    )
    op.create_table('watchlists',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('query', sa.String(length=200), nullable=True),
    sa.Column('brand_slugs', postgresql.ARRAY(sa.String(length=120)), server_default='{}', nullable=False),
    sa.Column('category_slugs', postgresql.ARRAY(sa.String(length=80)), server_default='{}', nullable=False),
    sa.Column('sizes', postgresql.ARRAY(sa.String(length=20)), server_default='{}', nullable=False),
    sa.Column('conditions', postgresql.ARRAY(sa.String(length=24)), server_default='{}', nullable=False),
    sa.Column('countries', postgresql.ARRAY(sa.String(length=2)), server_default='{}', nullable=False),
    sa.Column('vintage_only', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('max_buy_price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('min_profit', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('min_roi', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.Column('min_flip_score', sa.SmallInteger(), nullable=True),
    sa.Column('min_confidence', sa.SmallInteger(), nullable=True),
    sa.Column('max_risk_score', sa.SmallInteger(), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('notify', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('last_matched_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_watchlists_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_watchlists'))
    )
    op.create_index(op.f('ix_watchlists_user_id'), 'watchlists', ['user_id'], unique=False)
    op.create_table('listings',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('external_id', sa.String(length=64), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('description', sa.Text(), server_default='', nullable=False),
    sa.Column('price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('currency', sa.String(length=3), server_default='EUR', nullable=False),
    sa.Column('brand_raw', sa.String(length=120), nullable=True),
    sa.Column('brand_id', sa.Integer(), nullable=True),
    sa.Column('category_raw', sa.String(length=120), nullable=True),
    sa.Column('subcategory_raw', sa.String(length=120), nullable=True),
    sa.Column('category_id', sa.Integer(), nullable=True),
    sa.Column('size_raw', sa.String(length=60), nullable=True),
    sa.Column('size_normalized', sa.String(length=20), nullable=True),
    sa.Column('condition_raw', sa.String(length=60), nullable=True),
    sa.Column('condition', sa.String(length=24), server_default='unknown', nullable=False),
    sa.Column('color_raw', sa.String(length=60), nullable=True),
    sa.Column('color', sa.String(length=30), nullable=True),
    sa.Column('material_raw', sa.String(length=120), nullable=True),
    sa.Column('material', sa.String(length=30), nullable=True),
    sa.Column('country', sa.String(length=2), nullable=True),
    sa.Column('product_id', sa.UUID(), nullable=True),
    sa.Column('model_name', sa.String(length=120), nullable=True),
    sa.Column('gender', sa.String(length=10), nullable=True),
    sa.Column('is_vintage', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('product_code', sa.String(length=64), nullable=True),
    sa.Column('identification_confidence', sa.SmallInteger(), nullable=True),
    sa.Column('identification', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('seller_id', sa.UUID(), nullable=True),
    sa.Column('buyer_protection_fee', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('shipping_fee', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('buyer_protection_available', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('favourite_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('view_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('photo_count', sa.SmallInteger(), server_default='0', nullable=False),
    sa.Column('status', sa.String(length=16), server_default='active', nullable=False),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('status_changed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sold_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('removed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('duplicate_of_id', sa.UUID(), nullable=True),
    sa.Column('title_fingerprint', sa.String(length=64), nullable=True),
    sa.Column('raw', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('price >= 0', name=op.f('ck_listings_price_non_negative')),
    sa.ForeignKeyConstraint(['brand_id'], ['brands.id'], name=op.f('fk_listings_brand_id_brands'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['category_id'], ['categories.id'], name=op.f('fk_listings_category_id_categories'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['duplicate_of_id'], ['listings.id'], name=op.f('fk_listings_duplicate_of_id_listings'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], name=op.f('fk_listings_product_id_products'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['seller_id'], ['sellers.id'], name=op.f('fk_listings_seller_id_sellers'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_listings')),
    sa.UniqueConstraint('provider', 'external_id', name=op.f('uq_listings_provider_external_id'))
    )
    op.create_index(op.f('ix_listings_duplicate_of_id'), 'listings', ['duplicate_of_id'], unique=False)
    op.create_index(op.f('ix_listings_product_id'), 'listings', ['product_id'], unique=False)
    op.create_index('ix_listings_published_at', 'listings', ['published_at'], unique=False)
    op.create_index('ix_listings_segment', 'listings', ['brand_id', 'category_id', 'status'], unique=False)
    op.create_index(op.f('ix_listings_seller_id'), 'listings', ['seller_id'], unique=False)
    op.create_index(op.f('ix_listings_size_normalized'), 'listings', ['size_normalized'], unique=False)
    op.create_index('ix_listings_status_last_seen', 'listings', ['status', 'last_seen_at'], unique=False)
    op.create_index(op.f('ix_listings_title_fingerprint'), 'listings', ['title_fingerprint'], unique=False)
    op.create_index('ix_listings_title_trgm', 'listings', ['title'], unique=False, postgresql_using='gin', postgresql_ops={'title': 'gin_trgm_ops'})
    op.create_index(op.f('ix_listings_url'), 'listings', ['url'], unique=False)
    op.create_table('analysis_jobs',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('listing_id', sa.UUID(), nullable=True),
    sa.Column('job_type', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('priority', sa.String(length=8), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('queued_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['listing_id'], ['listings.id'], name=op.f('fk_analysis_jobs_listing_id_listings'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_analysis_jobs'))
    )
    op.create_index(op.f('ix_analysis_jobs_listing_id'), 'analysis_jobs', ['listing_id'], unique=False)
    op.create_index('ix_analysis_jobs_status_queued', 'analysis_jobs', ['status', 'queued_at'], unique=False)
    op.create_table('listing_images',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('listing_id', sa.UUID(), nullable=False),
    sa.Column('position', sa.SmallInteger(), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('phash', sa.String(length=16), nullable=True),
    sa.Column('width', sa.Integer(), nullable=True),
    sa.Column('height', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['listing_id'], ['listings.id'], name=op.f('fk_listing_images_listing_id_listings'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_listing_images')),
    sa.UniqueConstraint('listing_id', 'position', name=op.f('uq_listing_images_listing_id_position'))
    )
    op.create_index(op.f('ix_listing_images_phash'), 'listing_images', ['phash'], unique=False)
    op.create_table('listing_price_history',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('listing_id', sa.UUID(), nullable=False),
    sa.Column('price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('currency', sa.String(length=3), server_default='EUR', nullable=False),
    sa.Column('observed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['listing_id'], ['listings.id'], name=op.f('fk_listing_price_history_listing_id_listings'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_listing_price_history'))
    )
    op.create_index('ix_price_history_listing_observed', 'listing_price_history', ['listing_id', 'observed_at'], unique=False)
    op.create_table('market_comparables',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('listing_id', sa.UUID(), nullable=False),
    sa.Column('comparable_listing_id', sa.UUID(), nullable=False),
    sa.Column('similarity', sa.Numeric(precision=8, scale=4), nullable=False),
    sa.Column('price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('adjusted_price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('weight', sa.Numeric(precision=10, scale=4), nullable=False),
    sa.Column('is_sold', sa.Boolean(), nullable=False),
    sa.Column('included', sa.Boolean(), nullable=False),
    sa.Column('exclusion_reason', sa.String(length=40), nullable=True),
    sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['comparable_listing_id'], ['listings.id'], name=op.f('fk_market_comparables_comparable_listing_id_listings'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['listing_id'], ['listings.id'], name=op.f('fk_market_comparables_listing_id_listings'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_market_comparables')),
    sa.UniqueConstraint('listing_id', 'comparable_listing_id', name=op.f('uq_market_comparables_listing_id_comparable_listing_id'))
    )
    op.create_index(op.f('ix_market_comparables_listing_id'), 'market_comparables', ['listing_id'], unique=False)
    op.create_table('opportunities',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('listing_id', sa.UUID(), nullable=False),
    sa.Column('product_id', sa.UUID(), nullable=True),
    sa.Column('algorithm_version', sa.String(length=32), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('listing_price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('fair_market_value', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('market_median', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('market_mean', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('market_p25', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('market_p75', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('market_min', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('market_max', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('quick_sale_price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('expected_sale_price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('optimistic_sale_price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('discount_vs_market', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.Column('total_acquisition_cost', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('expected_net_revenue', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('expected_profit', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('expected_roi', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.Column('conservative_profit', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('conservative_roi', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.Column('optimistic_profit', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('optimistic_roi', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.Column('max_buy_price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('good_buy_price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('suggested_offer', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('demand_level', sa.String(length=16), nullable=True),
    sa.Column('demand_score', sa.SmallInteger(), nullable=True),
    sa.Column('sell_through_rate', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.Column('velocity_score', sa.SmallInteger(), nullable=True),
    sa.Column('estimated_days_to_sell', sa.Numeric(precision=6, scale=1), nullable=True),
    sa.Column('velocity_bucket', sa.String(length=8), nullable=True),
    sa.Column('flip_score', sa.SmallInteger(), nullable=False),
    sa.Column('confidence_score', sa.SmallInteger(), nullable=False),
    sa.Column('risk_score', sa.SmallInteger(), nullable=False),
    sa.Column('risk_level', sa.String(length=16), nullable=False),
    sa.Column('seller_score', sa.SmallInteger(), nullable=True),
    sa.Column('deal_tier', sa.String(length=16), nullable=False),
    sa.Column('is_ultra_deal', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('verdict', sa.String(length=10), nullable=False),
    sa.Column('recommended_action', sa.String(length=16), nullable=False),
    sa.Column('comparables_count', sa.Integer(), nullable=False),
    sa.Column('sold_comparables_count', sa.Integer(), nullable=False),
    sa.Column('identification_confidence', sa.SmallInteger(), nullable=True),
    sa.Column('score_breakdown', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('explanation', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('risk_factors', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('market_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('ai_analysis', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('ai_provider', sa.String(length=32), nullable=True),
    sa.Column('ai_analyzed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('analyzed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['listing_id'], ['listings.id'], name=op.f('fk_opportunities_listing_id_listings'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], name=op.f('fk_opportunities_product_id_products'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_opportunities')),
    sa.UniqueConstraint('listing_id', name=op.f('uq_opportunities_listing_id'))
    )
    op.create_index('ix_opportunities_active_flip', 'opportunities', ['is_active', sa.literal_column('flip_score DESC')], unique=False)
    op.create_index('ix_opportunities_analyzed_at', 'opportunities', ['analyzed_at'], unique=False)
    op.create_index(op.f('ix_opportunities_expected_profit'), 'opportunities', ['expected_profit'], unique=False)
    op.create_index(op.f('ix_opportunities_expected_roi'), 'opportunities', ['expected_roi'], unique=False)
    op.create_index('ix_opportunities_ultra', 'opportunities', ['is_ultra_deal'], unique=False, postgresql_where=sa.text('is_active'))
    op.create_table('alerts',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('type', sa.String(length=24), nullable=False),
    sa.Column('priority', sa.String(length=8), nullable=False),
    sa.Column('opportunity_id', sa.UUID(), nullable=True),
    sa.Column('listing_id', sa.UUID(), nullable=True),
    sa.Column('watchlist_id', sa.UUID(), nullable=True),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('dedupe_key', sa.String(length=200), nullable=False),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['listing_id'], ['listings.id'], name=op.f('fk_alerts_listing_id_listings'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['opportunity_id'], ['opportunities.id'], name=op.f('fk_alerts_opportunity_id_opportunities'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_alerts_user_id_users'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['watchlist_id'], ['watchlists.id'], name=op.f('fk_alerts_watchlist_id_watchlists'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_alerts')),
    sa.UniqueConstraint('user_id', 'dedupe_key', name=op.f('uq_alerts_user_id_dedupe_key'))
    )
    op.create_index('ix_alerts_user_created', 'alerts', ['user_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_index('ix_alerts_user_unread', 'alerts', ['user_id'], unique=False, postgresql_where=sa.text('read_at IS NULL'))
    op.create_table('favorites',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('listing_id', sa.UUID(), nullable=False),
    sa.Column('opportunity_id', sa.UUID(), nullable=True),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['listing_id'], ['listings.id'], name=op.f('fk_favorites_listing_id_listings'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['opportunity_id'], ['opportunities.id'], name=op.f('fk_favorites_opportunity_id_opportunities'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_favorites_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_favorites')),
    sa.UniqueConstraint('user_id', 'listing_id', name=op.f('uq_favorites_user_id_listing_id'))
    )
    op.create_index('ix_favorites_user_state', 'favorites', ['user_id', 'state'], unique=False)
    op.create_table('opportunity_scores',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('opportunity_id', sa.UUID(), nullable=False),
    sa.Column('algorithm_version', sa.String(length=32), nullable=False),
    sa.Column('listing_price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('flip_score', sa.SmallInteger(), nullable=False),
    sa.Column('confidence_score', sa.SmallInteger(), nullable=False),
    sa.Column('risk_score', sa.SmallInteger(), nullable=False),
    sa.Column('components', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('penalties', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('expected_roi', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['opportunity_id'], ['opportunities.id'], name=op.f('fk_opportunity_scores_opportunity_id_opportunities'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_opportunity_scores'))
    )
    op.create_index('ix_opportunity_scores_opp_time', 'opportunity_scores', ['opportunity_id', 'computed_at'], unique=False)
    op.create_table('purchases',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('listing_id', sa.UUID(), nullable=True),
    sa.Column('opportunity_id', sa.UUID(), nullable=True),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('brand_id', sa.Integer(), nullable=True),
    sa.Column('brand_name', sa.String(length=120), nullable=True),
    sa.Column('category_id', sa.Integer(), nullable=True),
    sa.Column('category_name', sa.String(length=120), nullable=True),
    sa.Column('size', sa.String(length=20), nullable=True),
    sa.Column('condition', sa.String(length=24), nullable=True),
    sa.Column('purchase_price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('buyer_protection_fee', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('shipping_cost', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('other_costs', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('total_cost', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('purchase_date', sa.Date(), nullable=False),
    sa.Column('expected_sale_price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('purchase_price >= 0', name=op.f('ck_purchases_purchase_price_non_negative')),
    sa.ForeignKeyConstraint(['brand_id'], ['brands.id'], name=op.f('fk_purchases_brand_id_brands'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['category_id'], ['categories.id'], name=op.f('fk_purchases_category_id_categories'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['listing_id'], ['listings.id'], name=op.f('fk_purchases_listing_id_listings'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['opportunity_id'], ['opportunities.id'], name=op.f('fk_purchases_opportunity_id_opportunities'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_purchases_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_purchases'))
    )
    op.create_index('ix_purchases_user_date', 'purchases', ['user_id', 'purchase_date'], unique=False)
    op.create_table('alert_deliveries',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('alert_id', sa.UUID(), nullable=False),
    sa.Column('channel', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['alert_id'], ['alerts.id'], name=op.f('fk_alert_deliveries_alert_id_alerts'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_alert_deliveries')),
    sa.UniqueConstraint('alert_id', 'channel', name=op.f('uq_alert_deliveries_alert_id_channel'))
    )
    op.create_table('inventory',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('purchase_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('listed_price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('estimated_value', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('listed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['purchase_id'], ['purchases.id'], name=op.f('fk_inventory_purchase_id_purchases'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_inventory_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_inventory')),
    sa.UniqueConstraint('purchase_id', name=op.f('uq_inventory_purchase_id'))
    )
    op.create_index('ix_inventory_user_status', 'inventory', ['user_id', 'status'], unique=False)
    op.create_table('sales',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('purchase_id', sa.UUID(), nullable=False),
    sa.Column('sale_price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('selling_fees', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('shipping_cost', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('packaging_cost', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('other_costs', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('net_revenue', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('profit', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('roi', sa.Numeric(precision=8, scale=4), nullable=False),
    sa.Column('holding_days', sa.Integer(), nullable=False),
    sa.Column('sale_date', sa.Date(), nullable=False),
    sa.Column('platform', sa.String(length=32), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('sale_price >= 0', name=op.f('ck_sales_sale_price_non_negative')),
    sa.ForeignKeyConstraint(['purchase_id'], ['purchases.id'], name=op.f('fk_sales_purchase_id_purchases'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_sales_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sales')),
    sa.UniqueConstraint('purchase_id', name=op.f('uq_sales_purchase_id'))
    )
    op.create_index('ix_sales_user_date', 'sales', ['user_id', 'sale_date'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_sales_user_date', table_name='sales')
    op.drop_table('sales')
    op.drop_index('ix_inventory_user_status', table_name='inventory')
    op.drop_table('inventory')
    op.drop_table('alert_deliveries')
    op.drop_index('ix_purchases_user_date', table_name='purchases')
    op.drop_table('purchases')
    op.drop_index('ix_opportunity_scores_opp_time', table_name='opportunity_scores')
    op.drop_table('opportunity_scores')
    op.drop_index('ix_favorites_user_state', table_name='favorites')
    op.drop_table('favorites')
    op.drop_index('ix_alerts_user_unread', table_name='alerts', postgresql_where=sa.text('read_at IS NULL'))
    op.drop_index('ix_alerts_user_created', table_name='alerts')
    op.drop_table('alerts')
    op.drop_index('ix_opportunities_ultra', table_name='opportunities', postgresql_where=sa.text('is_active'))
    op.drop_index(op.f('ix_opportunities_expected_roi'), table_name='opportunities')
    op.drop_index(op.f('ix_opportunities_expected_profit'), table_name='opportunities')
    op.drop_index('ix_opportunities_analyzed_at', table_name='opportunities')
    op.drop_index('ix_opportunities_active_flip', table_name='opportunities')
    op.drop_table('opportunities')
    op.drop_index(op.f('ix_market_comparables_listing_id'), table_name='market_comparables')
    op.drop_table('market_comparables')
    op.drop_index('ix_price_history_listing_observed', table_name='listing_price_history')
    op.drop_table('listing_price_history')
    op.drop_index(op.f('ix_listing_images_phash'), table_name='listing_images')
    op.drop_table('listing_images')
    op.drop_index('ix_analysis_jobs_status_queued', table_name='analysis_jobs')
    op.drop_index(op.f('ix_analysis_jobs_listing_id'), table_name='analysis_jobs')
    op.drop_table('analysis_jobs')
    op.drop_index(op.f('ix_listings_url'), table_name='listings')
    op.drop_index('ix_listings_title_trgm', table_name='listings', postgresql_using='gin', postgresql_ops={'title': 'gin_trgm_ops'})
    op.drop_index(op.f('ix_listings_title_fingerprint'), table_name='listings')
    op.drop_index('ix_listings_status_last_seen', table_name='listings')
    op.drop_index(op.f('ix_listings_size_normalized'), table_name='listings')
    op.drop_index(op.f('ix_listings_seller_id'), table_name='listings')
    op.drop_index('ix_listings_segment', table_name='listings')
    op.drop_index('ix_listings_published_at', table_name='listings')
    op.drop_index(op.f('ix_listings_product_id'), table_name='listings')
    op.drop_index(op.f('ix_listings_duplicate_of_id'), table_name='listings')
    op.drop_table('listings')
    op.drop_index(op.f('ix_watchlists_user_id'), table_name='watchlists')
    op.drop_table('watchlists')
    op.drop_table('user_preferences')
    op.drop_index(op.f('ix_user_affinities_user_id'), table_name='user_affinities')
    op.drop_table('user_affinities')
    op.drop_index(op.f('ix_push_subscriptions_user_id'), table_name='push_subscriptions')
    op.drop_table('push_subscriptions')
    op.drop_index(op.f('ix_products_category_id'), table_name='products')
    op.drop_index(op.f('ix_products_brand_id'), table_name='products')
    op.drop_table('products')
    op.drop_table('notification_settings')
    op.drop_index('ix_market_statistics_brand_category', table_name='market_statistics')
    op.drop_table('market_statistics')
    op.drop_table('users')
    op.drop_table('system_state')
    op.drop_table('sellers')
    op.drop_index(op.f('ix_categories_parent_id'), table_name='categories')
    op.drop_table('categories')
    op.drop_table('brands')
