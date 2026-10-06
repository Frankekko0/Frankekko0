"""Compact market summary for the browser extension's instant verdict.

The extension downloads it (a few tens of KB, refreshed every few hours) and keeps it locally,
so when a search page opens it can score every card in its service worker without waiting for
the server: brand and category recognised from the card, realized prices of that segment,
share sold within 30 days, the user's costs (same formulas as the server) and the counterfeit
rules. The full server analysis arrives a moment later and replaces the quick estimate.

Nothing in it is invented: a segment with fewer than ``MIN_SOLD`` sales is left out and the
card is shown as "dati insufficienti".
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import UserEconomics
from app.authenticity.assess import MAX_P, brand_rules, load_rules
from app.identification.taxonomy import SUSPICIOUS_PATTERNS, fold
from app.ingestion.catalog import Catalog
from app.opportunities.insights import HORIZON_DAYS, MIN_OUTCOMES
from app.pricing.comparables import CONDITION_MULTIPLIER
from app.pricing.market_value import SOLD_ONLY_MIN

MIN_SOLD = 5
WINDOW_DAYS = 120

_SEGMENTS = text(
    """
    SELECT brand_id, category_id,
           count(*) FILTER (WHERE status = 'sold' AND sold_at >= :since) AS n_sold,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY price) FILTER (WHERE status = 'sold' AND sold_at >= :since) AS median,
           percentile_cont(0.1) WITHIN GROUP (ORDER BY price) FILTER (WHERE status = 'sold' AND sold_at >= :since) AS p10,
           percentile_cont(0.9) WITHIN GROUP (ORDER BY price) FILTER (WHERE status = 'sold' AND sold_at >= :since) AS p90,
           avg(extract(epoch FROM sold_at - published_at) / 86400)
               FILTER (WHERE status = 'sold' AND sold_at >= :since AND published_at IS NOT NULL) AS days,
           count(*) FILTER (WHERE published_at IS NOT NULL AND (
               (status = 'sold' AND sold_at - published_at <= :horizon))) AS sold_in_time,
           count(*) FILTER (WHERE published_at IS NOT NULL AND (
               status = 'removed'
               OR (status = 'sold' AND sold_at - published_at > :horizon)
               OR (status = 'active' AND :now - published_at > :horizon))) AS not_in_time
    FROM listings
    WHERE brand_id IS NOT NULL AND duplicate_of_id IS NULL
      AND coalesce(sold_at, removed_at, last_seen_at, published_at) >= :since
    GROUP BY GROUPING SETS ((brand_id, category_id), (brand_id))
    """
)


def _p_sale(succ: int, fail: int) -> float | None:
    n = succ + fail
    return round((succ + 1) / (n + 2), 3) if n >= MIN_OUTCOMES else None


async def build_market_cache(session: AsyncSession, catalog: Catalog, econ: UserEconomics) -> dict[str, Any]:
    now = datetime.now(UTC)
    rows = (
        await session.execute(
            _SEGMENTS,
            {"since": now - timedelta(days=WINDOW_DAYS), "horizon": timedelta(days=HORIZON_DAYS), "now": now},
        )
    ).all()
    segments: dict[str, list[Any]] = {}
    for r in rows:
        brand = catalog.brand_slug(r.brand_id)
        if not brand or (r.n_sold or 0) < MIN_SOLD or r.median is None:
            continue
        cat = catalog.category_slug(r.category_id) if r.category_id is not None else "*"
        if cat is None:
            continue
        segments[f"{brand}|{cat}"] = [
            round(float(r.median), 2),
            round(float(r.p10), 2),
            round(float(r.p90), 2),
            int(r.n_sold),
            _p_sale(int(r.sold_in_time or 0), int(r.not_in_time or 0)),
            round(float(r.days), 1) if r.days is not None else None,
        ]

    tax = catalog.taxonomy
    brands = []
    for slug, info in sorted(catalog.brands_by_slug.items()):
        spec = tax.brand_by_slug.get(slug)
        aliases = sorted({fold(a) for a in (info.name, *(spec.aliases if spec else ())) if len(fold(a)) >= 2})
        rules = brand_rules(slug)
        brands.append(
            [slug, info.name, aliases, round(info.counterfeit_risk, 3), rules.get("price_floor_ratio", 0.45)]
        )
    categories = [
        [c.slug, c.parent, sorted({fold(k) for k in c.keywords})] for c in tax.leaf_categories() if c.keywords
    ]
    lines = [
        [b.slug, line.category, sorted({fold(k) for k in line.keywords})]
        for b in tax.all_brands
        for line in b.lines
    ]
    c = econ.costs
    rules = load_rules()
    payload: dict[str, Any] = {
        "costs": {
            "buyer_protection_fixed": float(c.buyer_protection_fixed),
            "buyer_protection_pct": float(c.buyer_protection_pct),
            "shipping_in": float(c.shipping_in),
            "use_listing_shipping": c.use_listing_shipping,
            "other_acquisition": float(c.other_acquisition),
            "selling_fee_fixed": float(c.selling_fee_fixed),
            "selling_fee_pct": float(c.selling_fee_pct),
            "payment_fee_fixed": float(c.payment_fee_fixed),
            "payment_fee_pct": float(c.payment_fee_pct),
            "advertising": float(c.advertising),
            "packaging": float(c.packaging),
            "shipping_out": float(c.shipping_out),
            "other_sale": float(c.other_sale),
        },
        "targets": {"min_profit": float(econ.targets.min_profit), "min_roi": float(econ.targets.min_roi)},
        "condition_mult": {str(k): v for k, v in CONDITION_MULTIPLIER.items()},
        "brands": brands,
        "categories": categories,
        "lines": lines,
        "segments": segments,
        "auth": {
            "lr": {
                k: rules["likelihood_ratios"][k]
                for k in ("price_far_below", "price_below", "suspicious_text")
            },
            "suspicious": [[p, label] for p, label in SUSPICIOUS_PATTERNS],
            "max_p": MAX_P,
        },
        "min_sold": MIN_SOLD,
        "sold_only_min": SOLD_ONLY_MIN,
        "horizon_days": HORIZON_DAYS,
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["version"] = hashlib.sha256(body.encode()).hexdigest()[:16]
    payload["generated_at"] = now.isoformat()
    return payload
