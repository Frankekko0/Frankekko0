"""Re-read the identity of listings already stored, with the current identification engine.

Listings captured before a fix keep what the old code made of them: a condition "Ottime" stored as ``unknown``, a
football shirt that is a jacket, a misspelt brand taken for a fact. Their raw fields (``brand_raw``, ``condition_raw``,
``category_raw``...) are kept, so the identity columns can be recomputed from them.

    python -m app.ingestion.reidentify            # dry run: what would change, nothing written
    python -m app.ingestion.reidentify --apply    # write it
    python -m app.ingestion.reidentify --apply --analyze   # ...and re-run the analysis of the changed listings

It only fills or corrects: a column the new reading leaves empty keeps its value (a brand a photo analysis read is
not erased because the title alone does not say it), and the extra keys of ``identification`` (the photo analysis
and its findings) are kept. Nothing is sent anywhere and nothing is requested from Vinted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import configure_logging, get_logger
from app.db.models import Listing
from app.db.session import dispose_engine, session_scope
from app.ingestion.catalog import load_catalog
from app.ingestion.service import get_engine, identify_listing, listing_columns
from app.marketplace.base import ProviderListing

log = get_logger(__name__)

IDENTITY_COLUMNS = (
    "brand_id",
    "category_id",
    "size_normalized",
    "condition",
    "color",
    "material",
    "model_name",
    "gender",
    "product_code",
    "identification_confidence",
)
# Findings that come from the photos: a re-reading of the text alone does not replace them.
PHOTO_SOURCES = ("image", "image_label")


@dataclass
class Report:
    scanned: int = 0
    changed: int = 0
    by_field: Counter[str] = field(default_factory=Counter)
    changed_ids: list[uuid.UUID] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "changed": self.changed,
            "by_field": dict(self.by_field.most_common()),
        }


def _as_provider(row: Listing) -> ProviderListing:
    return ProviderListing(
        external_id=row.external_id,
        url=row.url,
        title=row.title,
        description=row.description or "",
        price=row.price,
        currency=row.currency,
        brand=row.brand_raw,
        category=row.category_raw,
        subcategory=row.subcategory_raw,
        size=row.size_raw,
        condition=row.condition_raw,
        color=row.color_raw,
        material=row.material_raw,
        country=row.country,
    )


def _merge_identification(old: dict[str, Any] | None, fresh: dict[str, Any]) -> dict[str, Any]:
    merged = {**(old or {}), **fresh}
    for key, value in (old or {}).items():
        was_photo = isinstance(value, dict) and value.get("source") in PHOTO_SOURCES and value.get("value")
        now_empty = isinstance(fresh.get(key), dict) and not fresh[key].get("value")
        if was_photo and now_empty:
            merged[key] = value
    return merged


async def reidentify(
    session: AsyncSession, *, apply: bool = False, batch: int = 500, limit: int | None = None
) -> Report:
    catalog = await load_catalog(session, force=True)
    engine = get_engine(catalog.taxonomy)
    report = Report()
    now = datetime.now(UTC)
    last: uuid.UUID | None = None
    while limit is None or report.scanned < limit:
        stmt = select(Listing).order_by(Listing.id).limit(batch)
        if last is not None:
            stmt = stmt.where(Listing.id > last)
        rows = (await session.execute(stmt)).scalars().all()
        if not rows:
            break
        for row in rows:
            last = row.id
            report.scanned += 1
            pl = _as_provider(row)
            ident = identify_listing(engine, pl)
            cols = listing_columns(pl, ident, catalog, row.provider, now)
            changed: list[str] = []
            for key in IDENTITY_COLUMNS:
                new, old = cols[key], getattr(row, key)
                if new is None or new == "unknown" or new == old:
                    continue  # nothing new to say never erases what is there
                changed.append(key)
                if apply:
                    setattr(row, key, new)
            merged = _merge_identification(row.identification, ident.as_dict())
            if merged != (row.identification or {}):
                changed.append("identification")
                if apply:
                    row.identification = merged
            if changed:
                report.changed += 1
                report.changed_ids.append(row.id)
                report.by_field.update(changed)
        if apply:
            await session.flush()
    return report


async def _main(apply: bool, analyze: bool, limit: int | None) -> None:
    async with session_scope() as session:
        report = await reidentify(session, apply=apply, limit=limit)
        print(json.dumps({"apply": apply, **report.as_dict()}, indent=1))  # noqa: T201 - a command-line report
        if apply and analyze and report.changed_ids:
            from app.opportunities.pipeline import AnalysisPipeline

            pipeline = AnalysisPipeline(session)
            for i in range(0, len(report.changed_ids), 50):
                await pipeline.analyze_many(report.changed_ids[i : i + 50])
            log.info("reidentify.analyzed", listings=len(report.changed_ids))
    await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    parser.add_argument("--analyze", action="store_true", help="re-run the analysis of the changed listings")
    parser.add_argument("--limit", type=int, default=None, help="stop after this many listings")
    args = parser.parse_args()
    configure_logging()
    asyncio.run(_main(args.apply, args.analyze, args.limit))


if __name__ == "__main__":
    main()
