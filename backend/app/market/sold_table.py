"""The sold-prices table: per model, what sold, at what price, how fast, from how many samples.

Prices are never blended across meanings. Each model row keeps apart:

* ``realized``  - prices really paid or received (the user's own purchases and resales);
* ``asking``    - the last price asked while a sold listing was still on sale ("ultimo prezzo
  richiesto visto"): a ceiling of the sale price, not the price paid;
* ``reported``  - sale prices published by other marketplaces (less reliable, labelled as such).

Time to sell is the median of ``days_to_sell`` (publication -> sale) when the publication date is
known; otherwise the median of the observed days (first sighting -> sale), a lower bound, and the
row says so. With fewer than ``MIN_SAMPLES`` sales the row is marked insufficient: it is shown, but
not as an estimate.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.tracking.queries import csv_cell

MIN_SAMPLES = 3
EXPORT_LIMIT = 5000

_SQL = """
SELECT b.name AS brand, c.name_it AS category, s.model_name AS model,
       count(*) AS n,
       count(*) FILTER (WHERE s.realized_price IS NOT NULL) AS n_realized,
       count(*) FILTER (WHERE s.asking_price IS NOT NULL) AS n_asking,
       count(*) FILTER (WHERE s.price_kind = 'reported') AS n_reported,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY s.realized_price) AS realized_median,
       min(s.realized_price) AS realized_min, max(s.realized_price) AS realized_max,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY s.asking_price) AS asking_median,
       percentile_cont(0.25) WITHIN GROUP (ORDER BY s.asking_price) AS asking_p25,
       percentile_cont(0.75) WITHIN GROUP (ORDER BY s.asking_price) AS asking_p75,
       min(s.asking_price) AS asking_min, max(s.asking_price) AS asking_max,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY s.price_eur) FILTER (WHERE s.price_kind = 'reported') AS reported_median,
       count(s.days_to_sell) AS n_days_published,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY s.days_to_sell) AS days_published_median,
       count(s.observed_days) AS n_days_observed,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY s.observed_days) AS days_observed_median,
       max(s.sold_at) AS last_sold_at
  FROM sold_sales s
  LEFT JOIN brands b ON b.id = s.brand_id
  LEFT JOIN categories c ON c.id = s.category_id
 WHERE s.is_outlier = false
   AND (CAST(:brand AS text) IS NULL OR b.slug = :brand)
   AND (CAST(:q AS text) IS NULL OR s.model_name ILIKE '%' || :q || '%')
 GROUP BY b.name, c.name_it, s.model_name
 ORDER BY n DESC, last_sold_at DESC
 LIMIT :limit
"""


def _num(v: Any) -> float | None:
    return None if v is None else round(float(v), 2)


def _row(r: Any) -> dict[str, Any]:
    days: dict[str, Any] | None = None
    if r.n_days_published:
        days = {"median": _num(r.days_published_median), "samples": r.n_days_published, "basis": "published"}
    elif r.n_days_observed:
        # First sighting -> sale: the listing may have been online before it was first seen.
        days = {"median": _num(r.days_observed_median), "samples": r.n_days_observed, "basis": "observed"}
    return {
        "brand": r.brand,
        "category": r.category,
        "model": r.model,
        "samples": {
            "total": r.n,
            "realized": r.n_realized,
            "asking": r.n_asking,
            "reported": r.n_reported,
        },
        "realized": {
            "median": _num(r.realized_median),
            "min": _num(r.realized_min),
            "max": _num(r.realized_max),
        }
        if r.n_realized
        else None,
        "asking": {
            "median": _num(r.asking_median),
            "p25": _num(r.asking_p25),
            "p75": _num(r.asking_p75),
            "min": _num(r.asking_min),
            "max": _num(r.asking_max),
        }
        if r.n_asking
        else None,
        "reported": {"median": _num(r.reported_median)} if r.n_reported else None,
        "days_to_sell": days,
        "last_sold_at": r.last_sold_at,
        "evidence": "sufficient" if r.n >= MIN_SAMPLES else "insufficient",
    }


async def sold_price_table(
    session: AsyncSession, *, brand: str | None = None, q: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    rows = (await session.execute(text(_SQL), {"brand": brand, "q": q, "limit": limit})).all()
    return [_row(r) for r in rows]


CSV_HEADER = [
    "Marca",
    "Categoria",
    "Modello",
    "Campioni",
    "Prezzo reale: n",
    "Prezzo reale: mediana",
    "Prezzo reale: min",
    "Prezzo reale: max",
    "Ultimo prezzo richiesto: n",
    "Ultimo prezzo richiesto: mediana",
    "Ultimo prezzo richiesto: p25",
    "Ultimo prezzo richiesto: p75",
    "Ultimo prezzo richiesto: min",
    "Ultimo prezzo richiesto: max",
    "Prezzo riportato da altre fonti: n",
    "Prezzo riportato da altre fonti: mediana",
    "Giorni per vendere (mediana)",
    "Giorni: base",
    "Giorni: campioni",
    "Ultima vendita",
    "Evidenza",
]


def csv_rows(table: list[dict[str, Any]]) -> Iterator[list[str]]:
    for r in table:
        real, ask, rep, days = (
            r["realized"] or {},
            r["asking"] or {},
            r["reported"] or {},
            r["days_to_sell"] or {},
        )
        last: datetime | None = r["last_sold_at"]
        yield [
            csv_cell(x)
            for x in (
                r["brand"],
                r["category"],
                r["model"],
                r["samples"]["total"],
                r["samples"]["realized"],
                real.get("median"),
                real.get("min"),
                real.get("max"),
                r["samples"]["asking"],
                ask.get("median"),
                ask.get("p25"),
                ask.get("p75"),
                ask.get("min"),
                ask.get("max"),
                r["samples"]["reported"],
                rep.get("median"),
                days.get("median"),
                {"published": "dalla pubblicazione", "observed": "dalla prima vista (minimo)"}.get(
                    days.get("basis", ""), ""
                ),
                days.get("samples"),
                last,
                "sufficiente" if r["evidence"] == "sufficient" else "insufficiente (meno di 3 vendite)",
            )
        ]


def to_csv(table: list[dict[str, Any]], delimiter: str = ",") -> str:
    buf = io.StringIO()
    buf.write("﻿")
    writer = csv.writer(buf, delimiter=delimiter)
    writer.writerow(CSV_HEADER)
    writer.writerows(csv_rows(table))
    return buf.getvalue()
