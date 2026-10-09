"""CSV exports of the permanent record: observations (history) and analyses.

Both start with the same identification columns - internal id, Vinted id, original URL - so every
row can be traced back to its listing. Text that comes from third parties (titles) is neutralized
against spreadsheet formulas by ``csv_cell``.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Analysis, Listing, ListingSnapshot, Opportunity
from app.tracking.queries import csv_cell

EXPORT_LIMIT = 100_000

OBSERVATION_COLUMNS: list[tuple[str, str]] = [
    ("listing_id", "ID interno"),
    ("vinted_id", "ID Vinted"),
    ("url", "URL originale"),
    ("title", "Titolo"),
    ("observed_at", "Osservato il"),
    ("acquisition_mode", "Fonte"),
    ("capture_level", "Livello dati"),
    ("reason", "Motivo della riga"),
    ("status", "Stato"),
    ("price", "Prezzo"),
    ("currency", "Valuta"),
    ("favourite_count", "Preferiti"),
    ("view_count", "Visualizzazioni"),
    ("photo_count", "Foto"),
    ("extension_version", "Versione estensione"),
    ("parser_version", "Versione parser"),
    ("note", "Nota"),
]


def _scope(
    stmt: Select[Any], listing_id: uuid.UUID | None, since: datetime | None, until: datetime | None, col: Any
) -> Select[Any]:
    if listing_id is not None:
        stmt = stmt.where(Listing.id == listing_id)
    if since is not None:
        stmt = stmt.where(col >= since)
    if until is not None:
        stmt = stmt.where(col <= until)
    return stmt


async def _stream(
    session: AsyncSession, header: list[str], stmt: Select[Any], to_row: Any, delimiter: str
) -> AsyncIterator[str]:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter)
    buf.write("﻿")  # BOM: spreadsheets detect UTF-8 (accents, €)
    writer.writerow(header)
    yield buf.getvalue()
    stream = await session.stream(stmt.limit(EXPORT_LIMIT).execution_options(yield_per=1000))
    async for r in stream:
        buf.seek(0)
        buf.truncate()
        writer.writerow([csv_cell(v) for v in to_row(r)])
        yield buf.getvalue()


async def export_observations(
    session: AsyncSession,
    *,
    listing_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    delimiter: str = ",",
) -> AsyncIterator[str]:
    """Every history row (append-only): what was seen, when, where from, and why it was recorded."""
    s = ListingSnapshot
    stmt = (
        select(
            Listing.id.label("listing_id"),
            Listing.external_id.label("vinted_id"),
            Listing.provider,
            Listing.url,
            Listing.title,
            s.observed_at,
            s.acquisition_mode,
            s.capture_level,
            s.reason,
            s.status,
            s.price,
            s.currency,
            s.favourite_count,
            s.view_count,
            s.photo_count,
            s.extension_version,
            s.parser_version,
            s.note,
        )
        .join(Listing, Listing.id == s.listing_id)
        .order_by(Listing.id, s.observed_at, s.id)
    )
    stmt = _scope(stmt, listing_id, since, until, s.observed_at)

    def row(r: Any) -> list[Any]:
        d = r._mapping
        return [
            ("" if k == "vinted_id" and d["provider"] != "vinted" else d[k]) for k, _ in OBSERVATION_COLUMNS
        ]

    async for chunk in _stream(session, [h for _, h in OBSERVATION_COLUMNS], stmt, row, delimiter):
        yield chunk


ANALYSIS_COLUMNS = [
    "ID analisi",
    "ID interno",
    "ID Vinted",
    "URL originale",
    "Titolo",
    "Creata il",
    "Fonte dei dati",
    "Versione schema",
    "Versione algoritmo",
    "Motivo",
    "Prezzo annuncio",
    "Costo totale",
    "Valore di mercato",
    "Profitto atteso",
    "ROI atteso",
    "Punteggio Flip",
    "Confidenza",
    "Rischio",
    "Verdetto",
    "Azione",
    "Qualità dati",
    "Comparabili usati",
    "Foto analizzate",
    "Corrente",
]


async def export_analyses(
    session: AsyncSession,
    *,
    listing_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    delimiter: str = ",",
) -> AsyncIterator[str]:
    """Every stored analysis with its headline numbers and the reason it was run."""
    a = Analysis
    stmt = (
        select(a, Listing.title, (Opportunity.analysis_id == a.id).label("is_current"))
        .join(Listing, Listing.id == a.listing_id)
        .outerjoin(Opportunity, Opportunity.listing_id == a.listing_id)
        .order_by(a.listing_id, a.created_at, a.id)
    )
    stmt = _scope(stmt, listing_id, since, until, a.created_at)

    def row(r: Any) -> list[Any]:
        rec: Analysis = r.Analysis
        eco, mkt, dec = rec.economic or {}, rec.market or {}, rec.decision or {}
        expected = (eco.get("scenarios") or {}).get("expected") or {}
        return [
            rec.id,
            rec.listing_id,
            rec.vinted_id,
            rec.url,
            r.title,
            rec.created_at,
            rec.source,
            rec.schema_version,
            rec.algorithm_version,
            rec.trigger,
            eco.get("listing_price"),
            eco.get("total_acquisition_cost"),
            mkt.get("fair_market_value"),
            expected.get("profit"),
            expected.get("roi"),
            dec.get("flip_score"),
            dec.get("confidence_score"),
            dec.get("risk_score"),
            dec.get("verdict"),
            dec.get("recommended_action"),
            mkt.get("data_quality"),
            (mkt.get("comparables") or {}).get("used_total"),
            "sì" if (rec.visual or {}).get("analysed") else "no",
            bool(r.is_current),
        ]

    async for chunk in _stream(session, ANALYSIS_COLUMNS, stmt, row, delimiter):
        yield chunk
