"""Queue the photo check of listings that have their photos here but no model analysis.

    python -m app.tools.vision_backfill              # the 20 newest
    python -m app.tools.vision_backfill --limit 100  # more (each one is a model request: mind the daily quota)
    python -m app.tools.vision_backfill --dry-run    # only count

For listings stored before ``AI_VISION_ALWAYS`` was switched on, or whose model call kept failing. Only listings
whose whole gallery was uploaded by the browser are taken; the server reads no Vinted page.
"""

from __future__ import annotations

import argparse
import asyncio

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import dispose_engine, session_scope
from app.workers.queue import close_queue
from app.workers.vision_queue import listings_awaiting_vision, model_vision_on, queue_vision


async def main(limit: int, dry_run: bool) -> None:
    if not model_vision_on(get_settings()):
        print("No model reads the photos (AI_API_KEY unset or AI_VISION_ENABLED=false): nothing to queue.")
        return
    async with session_scope() as s:
        ids = await listings_awaiting_vision(s, limit)
    if dry_run:
        print(f"{len(ids)} listings would be queued (limit {limit}).")
    else:
        queued = await queue_vision(ids)
        print(f"{len(ids)} listings found, {queued} queued (the rest was already waiting).")
        await close_queue()
    await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--limit", type=int, default=20, help="how many listings at most (newest first)")
    parser.add_argument("--dry-run", action="store_true", help="count only, queue nothing")
    args = parser.parse_args()
    configure_logging()
    asyncio.run(main(max(1, args.limit), args.dry_run))
