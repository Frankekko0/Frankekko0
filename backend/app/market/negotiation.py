"""Average negotiation discount measured on the user's own purchases.

The price of a Vinted listing seen as sold is the price it was exposed at, not always the price
paid: buyers make offers. The user's purchases tell by how much: for every purchase of a listing
FlipFinder saw on sale, ``1 - price paid / price asked`` (asked = the listing price in force on
the purchase day from the price history, else the last price seen on sale, else the listing
price). The discount is the median of those values (robust to a mistyped purchase), clipped to
[0, 50%], and only with at least 3 such purchases; otherwise it is not applied, and said so.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import DateTime, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Listing, ListingPriceHistory, Purchase
from app.market.state import get_state, set_state
from app.pricing.evidence import NEGOTIATION_KEY

MIN_PURCHASES = 3
MAX_DISCOUNT = 0.5
# Single values outside this range are data errors (paid = total with shipping, a wrong listing).
PLAUSIBLE = (-0.5, 0.9)


def discount_from_samples(samples: Sequence[float]) -> tuple[float | None, int]:
    """(discount, purchases measured): the clipped median, None below ``MIN_PURCHASES``."""
    values = [v for v in samples if PLAUSIBLE[0] <= v <= PLAUSIBLE[1]]
    if len(values) < MIN_PURCHASES:
        return None, len(values)
    return round(min(MAX_DISCOUNT, max(0.0, statistics.median(values))), 4), len(values)


def describe(discount: float | None, n: int) -> str:
    if discount is None:
        return (
            f"Servono almeno {MIN_PURCHASES} tuoi acquisti di articoli visti in vendita (ora {n}): "
            "sconto da trattativa non applicato ai prezzi Vinted."
        )
    return (
        f"Sconto medio da trattativa {round(discount * 100)}%, misurato su {n} tuoi acquisti "
        "(prezzo pagato rispetto al prezzo richiesto)."
    )


async def purchase_samples(session: AsyncSession) -> list[tuple[date, float]]:
    """(purchase date, 1 - paid / asked) of every purchase with a known asked price."""
    day_end = cast(Purchase.purchase_date, DateTime(timezone=True)) + timedelta(days=1)
    asked_then = (
        select(ListingPriceHistory.price)
        .where(
            ListingPriceHistory.listing_id == Purchase.listing_id, ListingPriceHistory.observed_at < day_end
        )
        .order_by(ListingPriceHistory.observed_at.desc())
        .limit(1)
        .scalar_subquery()
    )
    asked = func.coalesce(asked_then, Listing.last_active_price, Listing.price)
    rows = (
        await session.execute(
            select(Purchase.purchase_date, Purchase.purchase_price, asked.label("asked"))
            .join(Listing, Listing.id == Purchase.listing_id)
            .where(Purchase.purchase_price > 0)
        )
    ).all()
    return [
        (r.purchase_date, 1 - float(r.purchase_price) / float(r.asked))
        for r in rows
        if r.asked is not None and r.asked > 0
    ]


async def compute_negotiation_discount(session: AsyncSession) -> dict[str, Any]:
    """{"discount": float | None, "n": int, "note": str, "measured_at": iso}; stored in system_state."""
    samples = await purchase_samples(session)
    discount, n = discount_from_samples([v for _, v in samples])
    value = {
        "discount": discount,
        "n": n,
        "note": describe(discount, n),
        "measured_at": datetime.now(UTC).isoformat(),
    }
    await set_state(session, NEGOTIATION_KEY, value)
    return value


async def current_negotiation_discount(session: AsyncSession) -> dict[str, Any]:
    """The stored value (no recomputation): same shape as ``compute_negotiation_discount``."""
    value = await get_state(session, NEGOTIATION_KEY)
    if not value:
        return {"discount": None, "n": 0, "note": describe(None, 0), "measured_at": None}
    return {k: value.get(k) for k in ("discount", "n", "note", "measured_at")}
