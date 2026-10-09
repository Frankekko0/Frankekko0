"""In-store scanner: a quick answer while standing in front of an item (§3.5-D).

From what the user types or reads (brand, size, the price on the tag, optionally the text of the label) and the
market statistics already stored, it returns the identification, the most worth paying, a verdict and the
confidence in a fraction of a second. With too little data the answer is "not verified", never an invented price.
Photo and barcode recognition are not implemented (docs/LIMITATIONS.md): the text of a label can be pasted or
read by the device's own OCR and sent along.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.profit.calculator import CostProfile, max_buy_price, profit_for

MIN_SOLD = 5
LATENCY_TARGET_MS = 1500


@dataclass(frozen=True)
class MarketRef:
    median: float | None
    p25: float | None
    p75: float | None
    n_sold: int
    source: str  # which segment answered


@dataclass(frozen=True)
class ScanResult:
    verdict: str  # BUY | NEGOTIATE | PASS | NOT_VERIFIED
    shown_price: float
    max_buy_price: float | None
    max_buy_price_fast_sale: float | None
    expected_profit: float | None
    roi: float | None
    confidence: int
    reason: str
    identification: dict[str, Any]
    market: dict[str, Any]
    elapsed_ms: float

    def as_dict(self) -> dict[str, Any]:
        return {**self.__dict__}


def scan(
    *,
    shown_price: float,
    brand: str | None,
    category: str | None,
    size: str | None,
    ref: MarketRef | None,
    costs: CostProfile,
    min_profit: float,
    min_roi: float,
    started: float | None = None,
) -> ScanResult:
    t0 = started if started is not None else time.perf_counter()
    ident = {
        "brand": brand,
        "category": category,
        "size": size,
        "known": [k for k, v in (("brand", brand), ("category", category), ("size", size)) if v],
    }
    market = {
        "median": ref.median if ref else None,
        "p25": ref.p25 if ref else None,
        "p75": ref.p75 if ref else None,
        "n_sold": ref.n_sold if ref else 0,
        "source": ref.source if ref else None,
    }

    def done(verdict: str, reason: str, **kw: Any) -> ScanResult:
        return ScanResult(
            verdict, shown_price, kw.get("mb"), kw.get("mf"), kw.get("profit"), kw.get("roi"), kw.get("conf", 0), reason, ident, market,
            round((time.perf_counter() - t0) * 1000, 1),
        )  # fmt: skip

    if brand is None or ref is None or ref.median is None or ref.n_sold < MIN_SOLD:
        return done(
            "NOT_VERIFIED",
            "Dati insufficienti: servono la marca e almeno "
            f"{MIN_SOLD} vendite di articoli simili. Nessun prezzo stimato.",
        )
    mid = Decimal(str(ref.median))
    fast = Decimal(str(ref.p25 if ref.p25 is not None else ref.median * 0.8))
    mb = max_buy_price(mid, costs, Decimal(str(min_profit)), Decimal(str(min_roi)))
    mf = max_buy_price(fast, costs, Decimal(str(min_profit)), Decimal(str(min_roi)))
    if mb is None:
        return done(
            "PASS", "A nessun prezzo d'acquisto si raggiungono gli obiettivi con questo prezzo di rivendita."
        )
    price = Decimal(str(shown_price))
    res = profit_for(price, mid, costs)
    spread = ((ref.p75 or ref.median) - (ref.p25 or ref.median)) / ref.median if ref.median else 1.0
    conf = max(
        0,
        min(
            100,
            round(
                35 + min(ref.n_sold, 30) * 1.5 + (10 if size else 0) + (10 if category else 0) - spread * 25
            ),
        ),
    )
    common: dict[str, Any] = {
        "mb": float(mb),
        "mf": float(mf) if mf is not None else None,
        "profit": float(res.net_profit),
        "roi": float(res.roi) if res.roi is not None else None,
        "conf": conf,
    }
    if mf is not None and price <= mf:
        return done("BUY", f"Conviene anche nello scenario di vendita rapida (fino a {mf} €).", **common)
    if price <= mb:
        return done(
            "NEGOTIATE",
            f"Conviene solo se si vende al prezzo medio: tratta verso {mf if mf is not None else mb} €, massimo {mb} €.",
            **common,
        )
    return done("PASS", f"Sopra il massimo di {mb} €: lo sconto necessario è {price - mb} €.", **common)
