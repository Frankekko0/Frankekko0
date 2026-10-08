"""Does the extra price evidence make the estimates more accurate? Backtest variants and the gate.

The same subjects are estimated with only what was known before them, in several variants:

* ``listings`` - marketplace listings only (the estimate before the extra evidence);
* ``own_sales`` / ``own`` - plus the user's resales / plus resales and purchases;
* ``external_sales`` / ``external_own`` - the previous ones plus other marketplaces' prices;
* ``*_cap`` - the same with the optimistic price capped at the median new price.

Subjects are the sold listings (as in the calibration, target = last price seen) and the user's
resales (target = price received, estimated as of the purchase day). No look-ahead: a listing
counts with the status it had at the cutoff, an own record only if it happened before it, an
external price only if ``coalesce(source_date, observed_at)`` is before it - so prices found by
a search today cannot help estimate past sales, and the external data becomes measurable as the
cache ages.

When the negotiation discount is measured, Vinted prices are converted to the price paid in every
variant, both in the comparables and in the sold listings' targets (a change of unit, the same for
all variants): the comparison between variants is then about the extra evidence only.

Gate (``system_state`` ``evidence_gate``): on the newer half of the sales (same time split as
the calibration), production keeps a source only if it does not raise the mean absolute error in
EUR, measured on the subjects estimated in both variants. A source is measurable from
``MIN_AFFECTED`` test subjects whose estimate it actually entered; until then it stays on with its
low weight ("non ancora misurabile"). The new-price cap stays off until it is measured and does
not lower the share of sales inside the min-max range.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.backtest import (
    Case,
    Metrics,
    Row,
    by_brand_index,
    case_of,
    known_at,
    metrics,
    sold_subjects,
)
from app.db.models import ExternalPrice, Purchase, SoldSale, SystemState
from app.identification.taxonomy import fold
from app.ingestion.catalog import Catalog
from app.opportunities.engine import PRICING_COMPARABLES, estimate_from_similar
from app.opportunities.pipeline import EXTERNAL_COLUMNS, OWN_COLUMNS, external_ref, own_record
from app.pricing.comparables import ItemProfile, ScoredComparable, select_comparables
from app.pricing.evidence import (
    GATE_KEY,
    NEGOTIATION_KEY,
    NEW_CAP_NOTE,
    EvidenceGate,
    ExternalRef,
    OwnRecord,
    PriceEvidence,
    discount_from_state,
    evidence_for_subject,
)
from app.pricing.market_value import MarketEstimate, SegmentPrior, _euros, estimate_market_value

MIN_AFFECTED = 30
OWN_PURCHASE_DAY = time(12, 0)
# (use own purchases, use external, cap at new price) -> variant name.
VARIANTS: dict[tuple[bool, bool, bool], str] = {
    (False, False, False): "own_sales",
    (True, False, False): "own",
    (False, True, False): "external_sales",
    (True, True, False): "external_own",
    (False, False, True): "own_sales_cap",
    (True, False, True): "own_cap",
    (False, True, True): "external_sales_cap",
    (True, True, True): "external_own_cap",
}
ALL_VARIANTS = ("baseline", "listings", *VARIANTS.values())


@dataclass
class EvidencePool:
    """Own records and external prices by (brand slug, folded model), oldest first."""

    own: dict[tuple[str, str], list[OwnRecord]] = field(default_factory=dict)
    external: dict[tuple[str, str], list[ExternalRef]] = field(default_factory=dict)
    discount: float | None = None
    # The user's resales as subjects: (record, purchase day).
    own_subjects: list[tuple[OwnRecord, date]] = field(default_factory=list)

    @classmethod
    def build(
        cls,
        own: list[OwnRecord],
        external: list[ExternalRef],
        discount: float | None,
        purchase_days: dict[Any, date] | None = None,
    ) -> EvidencePool:
        pool = cls(discount=discount)
        own_by: dict[tuple[str, str], list[OwnRecord]] = defaultdict(list)
        ext_by: dict[tuple[str, str], list[ExternalRef]] = defaultdict(list)
        for r in own:
            if r.brand_slug and r.model_name:
                own_by[(r.brand_slug, fold(r.model_name))].append(r)
        for e in external:
            if e.brand_slug and e.model_name:
                ext_by[(e.brand_slug, fold(e.model_name))].append(e)
        pool.own = {k: sorted(v, key=lambda r: r.sold_at) for k, v in own_by.items()}
        pool.external = {k: sorted(v, key=lambda r: r.at) for k, v in ext_by.items()}
        days = purchase_days or {}
        pool.own_subjects = [
            (r, days[r.purchase_id])
            for r in own
            if r.source == "own_sale"
            and r.brand_slug
            and r.purchase_id in days
            and days[r.purchase_id] < r.sold_at.date()
        ]
        return pool

    def own_before(self, key: tuple[str, str], cutoff: datetime) -> list[OwnRecord]:
        rows = self.own.get(key, [])
        return rows[: bisect.bisect_left([r.sold_at for r in rows], cutoff)]

    def external_before(self, key: tuple[str, str], cutoff: datetime) -> list[ExternalRef]:
        rows = self.external.get(key, [])
        return rows[: bisect.bisect_left([r.at for r in rows], cutoff)]


async def load_evidence_pool(session: AsyncSession, catalog: Catalog) -> EvidencePool:
    own_rows = (
        await session.execute(
            select(*OWN_COLUMNS).where(
                SoldSale.source.in_(("own_sale", "own_purchase")), SoldSale.is_outlier.is_(False)
            )
        )
    ).all()
    own = [own_record(r, catalog) for r in own_rows]
    ext = [
        external_ref(r, catalog)
        for r in (
            await session.execute(select(*EXTERNAL_COLUMNS).where(ExternalPrice.is_outlier.is_(False)))
        ).all()
    ]
    ids = [r.purchase_id for r in own if r.source == "own_sale" and r.purchase_id is not None]
    days = {}
    if ids:
        days = {
            r.id: r.purchase_date
            for r in (
                await session.execute(select(Purchase.id, Purchase.purchase_date).where(Purchase.id.in_(ids)))
            )
        }
    state = await session.get(SystemState, NEGOTIATION_KEY)
    return EvidencePool.build(own, ext, discount_from_state(state.value if state else None), days)


@dataclass
class EvidenceBacktest:
    """Cases per variant (same subjects, same order) and the time split."""

    cases: dict[str, list[Case]]
    split: datetime
    discount: float | None

    def test(self, variant: str) -> list[Case]:
        return [c for c in self.cases[variant] if c.sold_at >= self.split]


def _copies(similar: list[ScoredComparable]) -> list[ScoredComparable]:
    return [replace(c) for c in similar]


def _baseline(
    subject: ItemProfile, similar: list[ScoredComparable], now: datetime, prior: SegmentPrior | None
):
    """The estimate before the sold-first rule (``backtest.baseline_estimator``)."""
    pool = [c for c in _copies(similar) if c.item.status in ("sold", "active")]
    return estimate_market_value(
        pool[:PRICING_COMPARABLES], subject.condition, now, prior, sold_only_min=None
    )


def _counts(market: MarketEstimate) -> dict[str, Any]:
    used = [c for c in market.comparables if c.included]
    return {
        "n_external": sum(1 for c in used if c.item.source in ("external_sold", "external_asking")),
        "n_own_purchase": sum(1 for c in used if c.item.source == "own_purchase"),
        "n_own_sale": sum(1 for c in used if c.item.source == "own_sale"),
        "capped": any(n.startswith(NEW_CAP_NOTE) for n in market.notes),
    }


def _scaled(est: MarketEstimate, factor: float) -> MarketEstimate:
    """An estimate in another unit (the baseline in prices paid when the discount is measured)."""
    if factor == 1.0 or est.expected_sale_price is None:
        return est
    return replace(
        est,
        expected_sale_price=_euros(float(est.expected_sale_price) * factor),
        quick_sale_price=_euros(float(est.quick_sale_price) * factor, "floor")
        if est.quick_sale_price
        else None,
        optimistic_sale_price=_euros(float(est.optimistic_sale_price) * factor)
        if est.optimistic_sale_price
        else None,
    )


def _variants(
    subject: ItemProfile,
    similar: list[ScoredComparable],
    cutoff: datetime,
    prior: SegmentPrior | None,
    own: list[OwnRecord],
    external: list[ExternalRef],
    discount: float | None,
    parent_of: Any,
) -> dict[str, tuple[MarketEstimate, dict[str, Any]]]:
    """Every variant's estimate of one subject; identical evidence is estimated once."""
    out: dict[str, tuple[MarketEstimate, dict[str, Any]]] = {}
    factor = 1 - discount if discount else 1.0
    base = _baseline(subject, similar, cutoff, prior)
    out["baseline"] = (_scaled(base, factor), _counts(base))
    plain_ev = (
        PriceEvidence(negotiation_discount=discount, gate=EvidenceGate(False, False, False))
        if discount
        else None
    )
    _, listings = estimate_from_similar(subject, _copies(similar), cutoff, prior, plain_ev)
    out["listings"] = (listings, _counts(listings))
    full = evidence_for_subject(
        subject, own, external, negotiation_discount=discount, gate=EvidenceGate(), parent_of=parent_of
    )
    memo: dict[tuple[Any, ...], tuple[MarketEstimate, dict[str, Any]]] = {}
    for (use_own, use_ext, cap), name in VARIANTS.items():
        ev = replace(
            full, gate=EvidenceGate(use_external=use_ext, use_own_purchases=use_own, use_new_cap=cap)
        )
        signature = (
            tuple(sorted((p.source, p.ref_id or 0) for p in ev.candidates())),
            cap and bool(ev.new_prices),
        )
        if not signature[0] and not signature[1]:
            out[name] = out["listings"]
            continue
        if signature not in memo:
            _, market = estimate_from_similar(subject, _copies(similar), cutoff, prior, ev)
            memo[signature] = (market, _counts(market))
        out[name] = memo[signature]
    return out


def run_evidence_backtest(
    rows: list[Row],
    catalog: Catalog,
    pool: EvidencePool,
    split: datetime,
    max_subjects: int = 1500,
    window_days: int = 120,
) -> EvidenceBacktest:
    """Every variant on the same subjects (the sold listings sampled as in the calibration, plus
    the user's resales), each estimated with only what was known before it."""
    by_brand = by_brand_index(rows)
    cases: dict[str, list[Case]] = {v: [] for v in ALL_VARIANTS}
    factor = 1 - pool.discount if pool.discount else 1.0

    def add(results: dict[str, tuple[MarketEstimate, dict[str, Any]]], **kw: Any) -> None:
        for name, (est, counts) in results.items():
            cases[name].append(case_of(est, **kw, **counts))

    for s in sold_subjects(rows, max_subjects):
        p = s.profile
        assert p.published_at is not None and p.sold_at is not None
        cutoff = p.published_at + timedelta(hours=1)
        candidates, prior = known_at(by_brand, catalog, s.brand_id, p.category, cutoff, window_days, s)
        subject = ItemProfile(**{**p.__dict__, "status": "active", "sold_at": None, "last_seen_at": cutoff})
        key = (p.brand or "", fold(p.model or ""))
        own = [r for r in pool.own_before(key, cutoff) if r.listing_id != p.id]
        external = pool.external_before(key, cutoff)
        similar = select_comparables(subject, candidates, cutoff, max_count=10_000)
        results = _variants(
            subject, similar, cutoff, prior, own, external, pool.discount, catalog.parent_slug
        )
        add(
            results,
            listing_id=p.id,
            segment=(p.brand, p.category),
            condition=p.condition,
            sold_at=p.sold_at,
            realized=s.realized * factor,  # type: ignore[operator]
            kind="vinted",
        )

    for rec, bought in pool.own_subjects:
        cutoff = datetime.combine(bought, OWN_PURCHASE_DAY, tzinfo=UTC)
        brand_id = catalog.brand_id(rec.brand_slug)
        if brand_id is None:
            continue
        candidates, prior = known_at(by_brand, catalog, brand_id, rec.category_slug, cutoff, window_days)
        subject = ItemProfile(
            id=None,
            title=rec.title,
            price=rec_price(rec),
            brand=rec.brand_slug,
            category=rec.category_slug,
            parent_category=catalog.parent_slug(rec.category_slug),
            model=rec.model_name,
            condition=rec.condition,
            size=rec.size,
            status="active",
            published_at=cutoff,
            last_seen_at=cutoff,
        )
        key = (rec.brand_slug or "", fold(rec.model_name or ""))
        own = [r for r in pool.own_before(key, cutoff) if r.purchase_id != rec.purchase_id]
        external = pool.external_before(key, cutoff)
        similar = select_comparables(subject, candidates, cutoff, max_count=10_000)
        results = _variants(
            subject, similar, cutoff, prior, own, external, pool.discount, catalog.parent_slug
        )
        add(
            results,
            listing_id=None,
            segment=(rec.brand_slug, rec.category_slug),
            condition=rec.condition,
            sold_at=rec.sold_at,
            realized=rec.price_eur,
            kind="own_sale",
        )
    return EvidenceBacktest(cases=cases, split=split, discount=pool.discount)


def rec_price(rec: OwnRecord) -> Decimal:
    return Decimal(str(round(rec.price_eur, 2)))


# ---------------------------------------------------------------------------- gate
def _paired(a: list[Case], b: list[Case]) -> tuple[Metrics, Metrics]:
    """Metrics of two variants on the subjects both of them estimated."""
    both = [
        i
        for i, (x, y) in enumerate(zip(a, b, strict=True))
        if x.expected is not None and y.expected is not None
    ]
    return metrics([a[i] for i in both]), metrics([b[i] for i in both])


def _overshoot(cases: list[Case]) -> float:
    """Mean amount by which the maximum resale price exceeds the real one (EUR)."""
    vals = [max(0.0, c.optimistic - c.realized) for c in cases if c.optimistic is not None]
    return sum(vals) / len(vals) if vals else 0.0


def _better_or_equal(with_source: Metrics, without: Metrics) -> bool:
    if with_source.mae_eur is None or without.mae_eur is None:
        return True
    return with_source.mae_eur <= without.mae_eur + 1e-9


def _fmt(m: Metrics) -> str:
    return f"€{m.mae_eur:.2f}" if m.mae_eur is not None else "n.d."


def decide_gate(bt: EvidenceBacktest) -> dict[str, Any]:
    """The ``evidence_gate`` state from a backtest (see the module docstring)."""
    test = {name: bt.test(name) for name in ALL_VARIANTS}
    n_subjects = len(test["listings"])
    notes: list[str] = []

    affected_own = [i for i, c in enumerate(test["own"]) if c.n_own_purchase > 0]
    if len(affected_own) >= MIN_AFFECTED:
        with_own, without_own = _paired(
            [test["own"][i] for i in affected_own], [test["own_sales"][i] for i in affected_own]
        )
        use_own = _better_or_equal(with_own, without_own)
        notes.append(
            f"Tuoi acquisti: errore medio {_fmt(with_own)} con, {_fmt(without_own)} senza su "
            f"{len(affected_own)} vendite di prova: {'usati' if use_own else 'esclusi'}."
        )
    else:
        use_own = True
        notes.append(
            f"Tuoi acquisti: non ancora misurabili ({len(affected_own)} vendite di prova su {MIN_AFFECTED} "
            "necessarie), usati con il peso di una vendita Vinted."
        )
    base = "own" if use_own else "own_sales"
    ext = "external_own" if use_own else "external_sales"

    affected_ext = [i for i, c in enumerate(test[ext]) if c.n_external > 0]
    if len(affected_ext) >= MIN_AFFECTED:
        with_ext, without_ext = _paired(
            [test[ext][i] for i in affected_ext], [test[base][i] for i in affected_ext]
        )
        use_external = _better_or_equal(with_ext, without_ext)
        notes.append(
            f"Dati di altri mercati: errore medio {_fmt(with_ext)} con, {_fmt(without_ext)} senza su "
            f"{len(affected_ext)} vendite di prova: {'usati' if use_external else 'esclusi'}."
        )
    else:
        use_external = True
        notes.append(
            f"Dati di altri mercati: non ancora misurabili ({len(affected_ext)} vendite di prova su "
            f"{MIN_AFFECTED} necessarie), usati con peso ridotto."
        )
    chosen = ext if use_external else base
    capped = f"{chosen}_cap"
    affected_cap = [i for i, c in enumerate(test[capped]) if c.capped]
    use_new_cap = False
    if len(affected_cap) >= MIN_AFFECTED:
        with_cap = [test[capped][i] for i in affected_cap]
        without_cap = [test[chosen][i] for i in affected_cap]
        # A cap only lowers the maximum: it helps if the maximum overshoots real sales less
        # without pushing more sales above it (range coverage at most 1 point lower).
        over_with, over_without = _overshoot(with_cap), _overshoot(without_cap)
        cov_with, cov_without = metrics(with_cap).in_range or 0.0, metrics(without_cap).in_range or 0.0
        use_new_cap = over_with < over_without and cov_with >= cov_without - 0.01
        notes.append(
            f"Prezzo da nuovo come tetto: massimo oltre il prezzo reale di €{over_with:.2f} con, "
            f"€{over_without:.2f} senza; vendite nell'intervallo {round(cov_with * 100)}% con, "
            f"{round(cov_without * 100)}% senza: {'attivo' if use_new_cap else 'spento'}."
        )
    else:
        notes.append(
            f"Prezzo da nuovo come tetto: spento finché non è misurabile ({len(affected_cap)} casi)."
        )
    if bt.discount:
        notes.append(
            f"Prezzi Vinted convertiti in prezzo pagato (sconto da trattativa {round(bt.discount * 100)}%) "
            "nelle stime e nei valori reali."
        )
    chosen_final = f"{chosen}_cap" if use_new_cap else chosen
    return {
        "measured_at": datetime.now(UTC).isoformat(),
        "without_external": metrics(test[base]).as_dict(),
        "with_external": metrics(test[ext]).as_dict(),
        "use_external": use_external,
        "use_own_purchases": use_own,
        "use_new_cap": use_new_cap,
        "n_subjects": n_subjects,
        "note": " ".join(notes),
        # Detail (not needed by production): the estimate before the extra evidence, how many
        # test subjects each source entered, and the variant production now follows.
        "listings_only": metrics(test["listings"]).as_dict(),
        "affected": {
            "own_purchases": len(affected_own),
            "external": len(affected_ext),
            "new_cap": len(affected_cap),
        },
        "variant": chosen_final,
        "negotiation_discount": bt.discount,
        "split_at": bt.split.isoformat(),
    }


def production_variant(gate: dict[str, Any]) -> str:
    return str(gate.get("variant") or "external_own")


async def store_gate(session: AsyncSession, gate: dict[str, Any]) -> None:
    from app.market.state import set_state

    await set_state(session, GATE_KEY, gate)


def report(bt: EvidenceBacktest) -> dict[str, Any]:
    """Every variant's metrics on the newer half, split by subject kind (for the CLI)."""
    out: dict[str, Any] = {}
    for name in ALL_VARIANTS:
        cases = bt.test(name)
        out[name] = {
            "all": metrics(cases).as_dict(),
            "vinted": metrics([c for c in cases if c.kind == "vinted"]).as_dict(),
            "own_sales": metrics([c for c in cases if c.kind == "own_sale"]).as_dict(),
        }
    return out
