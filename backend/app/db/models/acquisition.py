from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, SmallInteger, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow


class AcquisitionAttempt(Base):
    """Log of every attempt to read or refresh a listing, successful or not.

    Failures carry a readable ``message`` (shown in the UI) and the technical detail in
    ``detail`` (for the logs); nothing sensitive is stored (no cookies, tokens or page bodies).
    """

    __tablename__ = "acquisition_attempts"
    __table_args__ = (
        Index("ix_acquisition_attempts_started", "started_at"),
        Index("ix_acquisition_attempts_listing", "listing_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    listing_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("listings.id", ondelete="SET NULL"))
    vinted_id: Mapped[str | None] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(24))
    action: Mapped[str] = mapped_column(String(16))  # capture | refresh | enrich | import | email
    outcome: Mapped[str] = mapped_column(String(16))  # ok | unchanged | not_found | blocked | error | skipped
    http_status: Mapped[int | None] = mapped_column(SmallInteger)
    message: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=utcnow)
    duration_ms: Mapped[int | None] = mapped_column()
