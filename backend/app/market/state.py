"""Small helpers for the ``system_state`` key/value rows used by the price evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SystemState


async def get_state(session: AsyncSession, key: str) -> dict[str, Any] | None:
    value = (await session.execute(select(SystemState.value).where(SystemState.key == key))).scalar()
    return dict(value) if value else None


async def set_state(session: AsyncSession, key: str, value: dict[str, Any]) -> None:
    now = datetime.now(UTC)
    stmt = pg_insert(SystemState).values(key=key, value=value, updated_at=now)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["key"], set_={"value": stmt.excluded.value, "updated_at": now}
        )
    )


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
