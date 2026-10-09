"""The append-only event log: one place to write what the system did and why."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event


async def log_event(
    session: AsyncSession,
    kind: str,
    *,
    actor: str = "system",
    subject_type: str | None = None,
    subject_id: object | None = None,
    payload: dict[str, Any] | None = None,
    at: datetime | None = None,
) -> None:
    """Append one event (the database refuses to change or delete it afterwards)."""
    session.add(
        Event(
            kind=kind,
            actor=actor,
            subject_type=subject_type,
            subject_id=str(subject_id) if subject_id is not None else None,
            payload=payload or {},
            **({"at": at} if at is not None else {}),
        )
    )
