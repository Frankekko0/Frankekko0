"""Re-analyse stored listings with the current algorithm version.

    python -m app.tools.reanalyze             # active listings analysed by an older version
    python -m app.tools.reanalyze --all       # every active listing

Each run adds a new entry to the analysis history (old analyses are kept).
"""

from __future__ import annotations

import argparse
import asyncio
import time

from sqlalchemy import or_, select

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.models import Listing, Opportunity
from app.db.session import dispose_engine, session_scope
from app.domain.enums import ListingStatus
from app.opportunities.pipeline import AnalysisPipeline

log = get_logger("app.reanalyze")
BATCH = 200


async def main(everything: bool) -> None:
    version = get_settings().algorithm_version
    async with session_scope() as s:
        stmt = (
            select(Listing.id, Listing.brand_id, Listing.category_id)
            .outerjoin(Opportunity, Opportunity.listing_id == Listing.id)
            .where(Listing.status == ListingStatus.ACTIVE, Listing.duplicate_of_id.is_(None))
        )
        if not everything:
            stmt = stmt.where(or_(Opportunity.id.is_(None), Opportunity.algorithm_version != version))
        rows = (await s.execute(stmt)).all()
    # Grouped by brand and category so each batch shares one comparables pool.
    ids = [r.id for r in sorted(rows, key=lambda r: (r.brand_id or 0, r.category_id or 0))]
    start = time.perf_counter()
    for i in range(0, len(ids), BATCH):
        async with session_scope() as s:
            await AnalysisPipeline(s).analyze_many(ids[i : i + BATCH])
        print(f"\r{min(i + BATCH, len(ids))}/{len(ids)} analysed", end="", flush=True)
    print(f"\nDone: {len(ids)} listings with algorithm {version} in {time.perf_counter() - start:.0f}s")
    await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--all", action="store_true", help="re-analyse every active listing")
    configure_logging()
    asyncio.run(main(parser.parse_args().all))
