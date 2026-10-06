"""Quick evaluations for the browser extension: one compact record per Vinted item."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import UserEconomics
from app.db.models import Listing, Opportunity
from app.opportunities.queries import OpportunityQueries
from app.schemas.extension import QuickEval

LEVEL_RANK = {"ok": 0, "info": 0, "low": 1, "medium": 2, "high": 3}


def fake_risk(signals: list[dict[str, Any]] | None) -> str:
    """Counterfeit risk as the extension filters it. Unknown when the analysis could not check."""
    for s in signals or []:
        if s.get("code") == "possible_fake":
            if not s.get("verifiable", True):
                return "unknown"
            level = str(s.get("level") or "ok")
            return "none" if LEVEL_RANK.get(level, 0) == 0 else level
    return "unknown"


async def quick_evaluations(
    session: AsyncSession, user_id: uuid.UUID, econ: UserEconomics, listing_ids: list[uuid.UUID]
) -> list[QuickEval]:
    """Evaluations in the order of ``listing_ids``, with the user's own costs and targets."""
    if not listing_ids:
        return []
    ids = list(dict.fromkeys(listing_ids))
    listings = {
        li.id: li
        for li in (
            await session.execute(
                select(
                    Listing.id,
                    Listing.external_id,
                    Listing.url,
                    Listing.title,
                    Listing.size_normalized,
                    Listing.price,
                    Listing.currency,
                    Listing.status,
                    Listing.tracked_at,
                    Listing.capture_level,
                ).where(Listing.id.in_(ids))
            )
        ).all()
    }
    cards: dict[uuid.UUID, Any] = {}
    for start in range(0, len(ids), 100):
        for c in await OpportunityQueries(session, user_id, econ).card_by_listing_ids(
            ids[start : start + 100]
        ):
            cards[c.listing_id] = c
    signals = dict(
        (
            await session.execute(
                select(Opportunity.listing_id, Opportunity.score_breakdown["risk_signals"]).where(
                    Opportunity.listing_id.in_(ids)
                )
            )
        ).all()
    )
    out: list[QuickEval] = []
    for lid in ids:
        li = listings.get(lid)
        if li is None:
            continue
        c = cards.get(lid)
        insufficient = c is not None and c.data_quality == "insufficient"
        if c is None:
            reason = (
                "Solo il link: apri l'annuncio per leggerne i dati."
                if li.capture_level == "link"
                else "Non analizzato: l'annuncio non è in vendita."
            )
        elif insufficient:
            reason = c.insufficient_reason or "Dati insufficienti per una stima affidabile."
        else:
            reason = c.headline or (c.top_reasons[0].label if c.top_reasons else None)
        score = None
        if c is not None and not insufficient:
            score = c.personal_flip_score if c.personal_flip_score is not None else c.flip_score
        out.append(
            QuickEval(
                vinted_id=li.external_id,
                listing_id=li.id,
                opportunity_id=c.id if c else None,
                url=li.url,
                title=li.title,
                brand=c.brand.name if c and c.brand else None,
                size=(c.size if c else None) or li.size_normalized,
                image_url=c.image_url if c else None,
                status=li.status,
                tracked=li.tracked_at is not None,
                capture_level=li.capture_level,
                analysis_depth=c.analysis_depth if c else None,
                data_quality=c.data_quality if c else None,
                insufficient_reason=c.insufficient_reason if c else None,
                flip_score=score,
                confidence=c.confidence_score if c else None,
                risk_level=c.risk_level if c else None,
                fake_risk=fake_risk(signals.get(lid)) if c else "unknown",
                price=li.price,
                currency=li.currency,
                total_cost=c.total_acquisition_cost if c else None,
                resale_expected=None if insufficient or c is None else c.expected_sale_price,
                net_margin=None if insufficient or c is None else c.expected_profit,
                roi=None if insufficient or c is None else c.expected_roi,
                days_to_sell=None if insufficient or c is None else c.estimated_days_to_sell,
                reason=reason,
                analyzed_at=c.analyzed_at if c else None,
            )
        )
    return out
