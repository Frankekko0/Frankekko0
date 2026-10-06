"""Remove photos and seller data that never belonged to a listing, then re-analyse it.

    python -m app.tools.clean_images --dry-run   # what would be removed
    python -m app.tools.clean_images             # remove and re-analyse

The worker runs the same cleanup at startup and every day; this tool does it on demand.
"""

from __future__ import annotations

import argparse
import asyncio

from app.core.logging import configure_logging
from app.db.session import dispose_engine, session_scope
from app.media.cleanup import clean_foreign_data
from app.opportunities.pipeline import AnalysisPipeline

BATCH = 200


async def main(dry_run: bool) -> None:
    async with session_scope() as s:
        report = await clean_foreign_data(s, dry_run=dry_run)
    print(
        f"{'Would remove' if dry_run else 'Removed'} {report.images_removed} foreign images, "
        f"reset {report.sellers_reset} copied seller profiles; {len(report.listing_ids)} listings affected."
    )
    if not dry_run:
        ids = report.listing_ids
        for i in range(0, len(ids), BATCH):
            async with session_scope() as s:
                await AnalysisPipeline(s).analyze_many(ids[i : i + BATCH])
        print(f"Re-analysed {len(ids)} listings.")
    await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true")
    configure_logging()
    asyncio.run(main(parser.parse_args().dry_run))
