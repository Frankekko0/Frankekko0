from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Ratio, utcnow

if TYPE_CHECKING:
    from app.db.models.listing import Listing


class Opportunity(Base):
    """Current analysis of a listing (one row per listing, re-computed on change)."""

    __tablename__ = "opportunities"
    __table_args__ = (
        Index("ix_opportunities_active_flip", "is_active", text("flip_score DESC")),
        Index("ix_opportunities_ultra", "is_ultra_deal", postgresql_where=text("is_active")),
        Index("ix_opportunities_analyzed_at", "analyzed_at"),
        Index("ix_opportunities_active_rap", "is_active", text("risk_adjusted_profit DESC NULLS LAST")),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), unique=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    algorithm_version: Mapped[str] = mapped_column(String(32))
    # How the analysed data was acquired and how deep the analysis is: "quick" for a card seen
    # while scrolling (title, price, one photo), "full" for an item page or a provider listing.
    acquisition_mode: Mapped[str | None] = mapped_column(String(24))
    analysis_depth: Mapped[str] = mapped_column(String(8), default="full", server_default="full")
    # "ok" | "limited" (few comparables: indicative) | "insufficient" (no reliable estimate:
    # the score is not shown and no alert is sent).
    data_quality: Mapped[str] = mapped_column(String(16), default="ok", server_default="ok")
    insufficient_reason: Mapped[str | None] = mapped_column(String(300))
    # The main reason in one line (feed, extension, tracking page).
    headline: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    listing_price: Mapped[Decimal] = mapped_column()
    currency: Mapped[str] = mapped_column(String(3), default="EUR")

    fair_market_value: Mapped[Decimal | None] = mapped_column()
    market_median: Mapped[Decimal | None] = mapped_column()
    market_mean: Mapped[Decimal | None] = mapped_column()
    market_p25: Mapped[Decimal | None] = mapped_column()
    market_p75: Mapped[Decimal | None] = mapped_column()
    market_min: Mapped[Decimal | None] = mapped_column()
    market_max: Mapped[Decimal | None] = mapped_column()
    quick_sale_price: Mapped[Decimal | None] = mapped_column()
    expected_sale_price: Mapped[Decimal | None] = mapped_column()
    optimistic_sale_price: Mapped[Decimal | None] = mapped_column()
    discount_vs_market: Mapped[Decimal | None] = mapped_column(Ratio)

    total_acquisition_cost: Mapped[Decimal] = mapped_column()
    expected_net_revenue: Mapped[Decimal | None] = mapped_column()
    expected_profit: Mapped[Decimal | None] = mapped_column(index=True)
    expected_roi: Mapped[Decimal | None] = mapped_column(Ratio, index=True)
    conservative_profit: Mapped[Decimal | None] = mapped_column()
    conservative_roi: Mapped[Decimal | None] = mapped_column(Ratio)
    optimistic_profit: Mapped[Decimal | None] = mapped_column()
    optimistic_roi: Mapped[Decimal | None] = mapped_column(Ratio)
    max_buy_price: Mapped[Decimal | None] = mapped_column()
    good_buy_price: Mapped[Decimal | None] = mapped_column()
    suggested_offer: Mapped[Decimal | None] = mapped_column()

    demand_level: Mapped[str | None] = mapped_column(String(16))
    demand_score: Mapped[int | None] = mapped_column(SmallInteger)
    sell_through_rate: Mapped[Decimal | None] = mapped_column(Ratio)
    velocity_score: Mapped[int | None] = mapped_column(SmallInteger)
    estimated_days_to_sell: Mapped[Decimal | None] = mapped_column(Numeric(6, 1))
    velocity_bucket: Mapped[str | None] = mapped_column(String(8))

    flip_score: Mapped[int] = mapped_column(SmallInteger)
    confidence_score: Mapped[int] = mapped_column(SmallInteger)
    risk_score: Mapped[int] = mapped_column(SmallInteger)
    risk_level: Mapped[str] = mapped_column(String(16))
    seller_score: Mapped[int | None] = mapped_column(SmallInteger)
    deal_tier: Mapped[str] = mapped_column(String(16))
    is_ultra_deal: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    verdict: Mapped[str] = mapped_column(String(10))
    recommended_action: Mapped[str] = mapped_column(String(16))

    comparables_count: Mapped[int] = mapped_column(Integer, default=0)
    sold_comparables_count: Mapped[int] = mapped_column(Integer, default=0)
    identification_confidence: Mapped[int | None] = mapped_column(SmallInteger)
    # Net margin x P(sold within 30 days) x P(authentic): the ranking key.
    risk_adjusted_profit: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    sale_probability: Mapped[Decimal | None] = mapped_column(Ratio)
    authenticity_probability: Mapped[Decimal | None] = mapped_column(Ratio)
    authenticity_verdict: Mapped[str | None] = mapped_column(String(24))

    score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    explanation: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    risk_factors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    market_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    ai_analysis: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ai_provider: Mapped[str | None] = mapped_column(String(32))
    ai_analyzed_at: Mapped[datetime | None] = mapped_column()

    analyzed_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow, onupdate=utcnow)

    listing: Mapped[Listing] = relationship(lazy="joined")


class OpportunityScore(Base):
    """Audit trail of every score computation (algorithm version, components, penalties)."""

    __tablename__ = "opportunity_scores"
    __table_args__ = (Index("ix_opportunity_scores_opp_time", "opportunity_id", "computed_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("opportunities.id", ondelete="CASCADE"))
    algorithm_version: Mapped[str] = mapped_column(String(32))
    acquisition_mode: Mapped[str | None] = mapped_column(String(24))
    analysis_depth: Mapped[str | None] = mapped_column(String(8))
    data_quality: Mapped[str | None] = mapped_column(String(16))
    listing_price: Mapped[Decimal] = mapped_column()
    flip_score: Mapped[int] = mapped_column(SmallInteger)
    confidence_score: Mapped[int] = mapped_column(SmallInteger)
    risk_score: Mapped[int] = mapped_column(SmallInteger)
    components: Mapped[dict[str, Any]] = mapped_column(JSONB)
    penalties: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    expected_roi: Mapped[Decimal | None] = mapped_column(Ratio)
    computed_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
