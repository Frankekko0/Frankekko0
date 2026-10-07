"""Price evidence beyond the marketplace listings, and where every number of an analysis comes from.

Besides the Vinted listings (sold = last price seen, on sale = asking price), an estimate can use:

* the user's own records: resales (price really received, weight 5) and purchases (price really
  paid, weight 1.5: exact, but the user buys below market, so the backtest gate decides whether
  they are kept at all);
* prices found by the external search on other marketplaces for the *same* model: concluded
  sales (weight 1) and asking prices (weight 0.5); new (retail) prices are never comparables,
  only a reference and, when the backtest proved it useful, a cap on the optimistic price.

Matching is strict: same brand and same model (folded), never a similar model. A Vinted sale is
the price last seen on the listing, not necessarily the price paid: when the negotiation discount
has been measured on the user's own purchases it is applied to every Vinted sale.

Everything here is pure (no I/O): the pipeline and the backtest load the rows and pass them in,
so production and the retroactive check compute exactly the same thing.
"""

from __future__ import annotations

import statistics
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.domain.enums import Condition
from app.identification.taxonomy import fold
from app.pricing.comparables import (
    ASK_SOURCES,
    SALE_SOURCES,
    SOURCE_WEIGHTS,
    ItemProfile,
    ScoredComparable,
    evidence_source,
    select_comparables,
)
from app.pricing.market_value import MarketEstimate, SegmentPrior, _euros

__all__ = [
    "ASK_SOURCES",
    "GATE_KEY",
    "NEGOTIATION_KEY",
    "NEW_CAP_NOTE",
    "SALE_SOURCES",
    "SOURCE_WEIGHTS",
    "EvidenceGate",
    "ExternalRef",
    "OwnRecord",
    "PriceEvidence",
    "build_provenance",
    "cap_at_new_price",
    "evidence_condition",
    "evidence_for_subject",
    "same_model",
    "with_evidence",
]

GATE_KEY = "evidence_gate"  # system_state: which sources the backtest allows (see analytics)
NEGOTIATION_KEY = "negotiation_discount"  # system_state: discount measured on own purchases
MAX_EXTERNAL_REFS = 12
MAX_NEW_SOURCES = 5
NEW_CONDITIONS = ("new_with_tags", "new_without_tags")
NEW_CAP_NOTE = "Prezzo massimo limitato al prezzo da nuovo"
_NEW_WORDS = {"new", "nuovo", "nuova", "neuf", "neu", "nuevo", "brand new", "nuovo con cartellino"}


def same_model(a: str | None, b: str | None) -> bool:
    """Strict model match: both known and equal once folded (case, accents, spaces)."""
    return bool(a) and bool(b) and fold(a) == fold(b)  # type: ignore[arg-type]


def evidence_condition(value: str | None) -> str:
    """A condition as the comparables expect it: the listing vocabulary when it is one, a generic
    "new" as new without tags, anything else (generic "used", unknown) as unknown."""
    v = (value or "").strip().lower()
    if v in Condition._value2member_map_:
        return v
    if v in _NEW_WORDS:
        return Condition.NEW_WITHOUT_TAGS.value
    return Condition.UNKNOWN.value


@dataclass(frozen=True)
class OwnRecord:
    """One of the user's purchases or resales (a ``sold_sales`` row of source own_*)."""

    id: int
    source: str  # own_sale | own_purchase
    title: str
    price_eur: float
    brand_slug: str | None
    category_slug: str | None
    model_name: str | None
    size: str | None
    condition: str
    sold_at: datetime
    published_at: datetime | None = None
    listing_id: uuid.UUID | None = None
    purchase_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ExternalRef:
    """A price found by the external search (an ``external_prices`` row)."""

    id: int
    kind: str  # new | asking | sold
    source: str  # "ebay.it", "zalando.it", ...
    price: float
    currency: str
    price_eur: float
    at: datetime  # coalesce(source_date, observed_at): when the price is known to be true
    condition: str
    url: str
    title: str
    brand_slug: str | None = None
    category_slug: str | None = None
    model_name: str | None = None
    size: str | None = None

    def as_dict(self, used: bool) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source": self.source,
            "price": round(self.price, 2),
            "currency": self.currency,
            "price_eur": round(self.price_eur, 2),
            "date": self.at.date().isoformat(),
            "condition": self.condition,
            "url": self.url,
            "title": self.title,
            "used_in_estimate": used,
        }


@dataclass(frozen=True)
class EvidenceGate:
    """Which extra sources production uses, as decided by the backtest (``system_state``
    ``evidence_gate``). Until measurable, external rows and own purchases are used with their low
    weights; the new-price cap stays off."""

    use_external: bool = True
    use_own_purchases: bool = True
    use_new_cap: bool = False
    measured_at: str | None = None
    note: str = "Non ancora misurabile: dati esterni e acquisti usati con peso ridotto."

    @classmethod
    def from_state(cls, value: dict[str, Any] | None) -> EvidenceGate:
        if not value:
            return cls()
        return cls(
            use_external=bool(value.get("use_external", True)),
            use_own_purchases=bool(value.get("use_own_purchases", True)),
            use_new_cap=bool(value.get("use_new_cap", False)),
            measured_at=value.get("measured_at"),
            note=str(value.get("note") or cls.note),
        )


def discount_from_state(value: dict[str, Any] | None) -> float | None:
    """The measured negotiation discount (None while fewer than 3 purchases measure it)."""
    if not value or value.get("discount") is None:
        return None
    d = float(value["discount"])
    return d if 0 < d < 1 else None


@dataclass
class PriceEvidence:
    """Extra evidence of one subject: own records and external rows of the same model."""

    own: list[ItemProfile] = field(default_factory=list)
    external: list[ItemProfile] = field(default_factory=list)
    refs: dict[int, ExternalRef] = field(default_factory=dict)  # every external row of the model
    new_prices: list[ExternalRef] = field(default_factory=list)
    negotiation_discount: float | None = None
    gate: EvidenceGate = field(default_factory=EvidenceGate)

    def candidates(self) -> list[ItemProfile]:
        """What may enter the pricing pool under the gate (own resales always do)."""
        out = [p for p in self.own if p.source == "own_sale" or self.gate.use_own_purchases]
        if self.gate.use_external:
            out += self.external
        return out

    @property
    def new_median(self) -> float | None:
        return statistics.median(r.price_eur for r in self.new_prices) if self.new_prices else None


def dedupe_refs(refs: Iterable[ExternalRef]) -> list[ExternalRef]:
    """One row per page: a sale beats an ask of the same URL, the newest observation wins."""
    rank = {"sold": 0, "asking": 1, "new": 2}
    best: dict[tuple[str, str], ExternalRef] = {}
    for r in sorted(refs, key=lambda r: (rank.get(r.kind, 3), -r.at.timestamp(), r.id)):
        group = "new" if r.kind == "new" else "used"
        key = (group, r.url or f"#{r.id}")
        if key not in best:
            best[key] = r
    return sorted(best.values(), key=lambda r: (rank.get(r.kind, 3), -r.at.timestamp(), r.id))


def own_profile(rec: OwnRecord, category: str | None, parent: str | None) -> ItemProfile:
    return ItemProfile(
        id=None,
        title=rec.title,
        price=Decimal(str(round(rec.price_eur, 2))),
        brand=rec.brand_slug,
        category=category,
        parent_category=parent,
        model=rec.model_name,
        condition=rec.condition or Condition.UNKNOWN.value,
        size=rec.size,
        status="sold",
        published_at=rec.published_at,
        sold_at=rec.sold_at,
        last_seen_at=rec.sold_at,
        source=rec.source,
        source_name="tracking",
        ref_id=rec.id,
        linked_listing_id=rec.listing_id,
    )


def external_profile(
    ref: ExternalRef, brand: str | None, category: str | None, parent: str | None
) -> ItemProfile:
    sold = ref.kind == "sold"
    return ItemProfile(
        id=None,
        title=ref.title,
        price=Decimal(str(round(ref.price_eur, 2))),
        brand=brand or ref.brand_slug,
        category=category,
        parent_category=parent,
        model=ref.model_name,
        condition=evidence_condition(ref.condition),
        size=ref.size,
        status="sold" if sold else "active",
        published_at=None if sold else ref.at,
        sold_at=ref.at if sold else None,
        last_seen_at=ref.at,
        source="external_sold" if sold else "external_asking",
        source_name=ref.source,
        ref_id=ref.id,
    )


def evidence_for_subject(
    subject: ItemProfile,
    own: Iterable[OwnRecord],
    refs: Iterable[ExternalRef],
    *,
    negotiation_discount: float | None,
    gate: EvidenceGate,
    parent_of: Callable[[str | None], str | None],
) -> PriceEvidence:
    """The evidence of one subject: rows of the same brand and model only. A row without a
    category takes the subject's (the model determines the category)."""
    ev = PriceEvidence(negotiation_discount=negotiation_discount, gate=gate)
    if not subject.brand or not subject.model:
        return ev

    def cat(row_cat: str | None) -> tuple[str | None, str | None]:
        if not row_cat or row_cat == subject.category:
            return subject.category, subject.parent_category
        return row_cat, parent_of(row_cat)

    for rec in own:
        if rec.brand_slug == subject.brand and same_model(rec.model_name, subject.model):
            ev.own.append(own_profile(rec, *cat(rec.category_slug)))
    matching = [
        r
        for r in refs
        if (r.brand_slug is None or r.brand_slug == subject.brand) and same_model(r.model_name, subject.model)
    ]
    for r in dedupe_refs(matching):
        ev.refs[r.id] = r
        if r.kind == "new":
            ev.new_prices.append(r)
        elif r.kind in ("sold", "asking"):
            ev.external.append(external_profile(r, subject.brand, *cat(r.category_slug)))
    return ev


def with_evidence(
    subject: ItemProfile, pool: list[ScoredComparable], evidence: PriceEvidence, now: datetime
) -> list[ScoredComparable]:
    """The pricing pool with the extra evidence merged in, most similar first.

    Vinted sales are brought to the price really paid (negotiation discount); a listing the user
    bought is counted once, as the purchase. The listing comparables are copied, never mutated.
    """
    d = evidence.negotiation_discount
    extra = [
        c
        for c in select_comparables(subject, evidence.candidates(), now, max_count=10_000)
        if same_model(c.item.model, subject.model)
    ]
    bought = {c.item.linked_listing_id for c in extra if c.item.source == "own_purchase"}
    out: list[ScoredComparable] = []
    for c in pool:
        if c.item.id is not None and c.item.id in bought:
            continue
        if d and c.item.source == "listing" and c.item.status == "sold":
            c = replace(c, adjusted_price=c.adjusted_price * (1 - d))
        out.append(c)
    if not extra:
        return out
    merged = out + extra
    merged.sort(key=lambda c: (c.similarity, c.weight), reverse=True)
    return merged


def cap_at_new_price(market: MarketEstimate, evidence: PriceEvidence, condition: str) -> MarketEstimate:
    """Used items never resell above the new (retail) price: the optimistic price is capped at the
    median new price. Applied only when the backtest showed it helps (``use_new_cap``)."""
    new_median = evidence.new_median
    if (
        new_median is None
        or condition in NEW_CONDITIONS
        or market.optimistic_sale_price is None
        or market.expected_sale_price is None
    ):
        return market
    cap = _euros(new_median)
    if market.optimistic_sale_price <= cap:
        return market
    return replace(
        market,
        optimistic_sale_price=max(cap, market.expected_sale_price),
        notes=[*market.notes, f"{NEW_CAP_NOTE} (€{new_median:.0f})."],
    )


# ---------------------------------------------------------------------------- provenance
def _n(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _sources_text(own: int, vinted: int, external: int) -> str:
    parts = []
    if own:
        parts.append(f"{own} {'tua' if own == 1 else 'tue'}")
    if vinted:
        parts.append(f"{vinted} Vinted")
    if external:
        parts.append(f"{external} da altri mercati")
    return ", ".join(parts)


def _pct(v: float) -> str:
    return f"{round(v * 100)}%"


def build_provenance(
    market: MarketEstimate,
    *,
    evidence: PriceEvidence | None,
    prior: SegmentPrior | None,
    calibrated: bool,
    calibration_n: int,
    velocity_days: float | None,
    velocity_n: int,
    days_basis: str,
    p_sale: dict[str, Any],
    new_cap_applied: bool = False,
) -> dict[str, Any]:
    """Where every number of the analysis comes from (stored in ``score_breakdown["provenance"]``,
    shown as-is by the web app: labels are short Italian sentences)."""
    used = [c for c in market.comparables if c.included]
    by_source = dict.fromkeys(SOURCE_WEIGHTS, 0)
    for c in used:
        by_source[evidence_source(c.item)] += 1
    own = by_source["own_sale"] + by_source["own_purchase"]
    sales = own + by_source["vinted_sold"] + by_source["external_sold"]
    asks = by_source["vinted_asking"] + by_source["external_asking"]
    discount = evidence.negotiation_discount if evidence is not None else None
    has_value = market.expected_sale_price is not None

    if not has_value:
        basis = "none"
    elif market.used_prior and market.n_used <= 3:
        basis = "prior"
    elif sales and asks:
        basis = "mixed"
    elif sales:
        basis = "sold"
    else:
        basis = "asking"

    sold_part = (
        f"{_n(sales, 'vendita conclusa', 'vendite concluse')} "
        f"({_sources_text(own, by_source['vinted_sold'], by_source['external_sold'])})"
    )
    ask_cut = 1 - market.ask_to_sale_ratio
    ask_part = (
        f"{_n(asks, 'prezzo richiesto', 'prezzi richiesti')} "
        f"({_sources_text(0, by_source['vinted_asking'], by_source['external_asking'])}"
        + (f", scontati del {_pct(ask_cut)})" if ask_cut >= 0.005 else ")")
    )
    if basis == "none":
        label = f"Dati insufficienti: solo {_n(market.n_used, 'comparabile utilizzabile', 'comparabili utilizzabili')}."
    elif basis == "asking":
        label = f"Nessuna vendita conclusa: mediana pesata di {ask_part}."
    elif basis == "mixed":
        label = f"Mediana pesata di {sold_part} e {ask_part}."
    else:
        label = f"Mediana pesata di {sold_part}."
    extras: list[str] = []
    if has_value and by_source["vinted_sold"]:
        extras.append(
            f"Vinted: ultimo prezzo rilevato, scontato del {_pct(discount)} (trattativa media sui tuoi acquisti)"
            if discount
            else "Vinted: ultimo prezzo rilevato (sconto da trattativa non ancora misurato)"
        )
    if has_value and market.used_prior:
        extras.append("affiancata alle statistiche del segmento")
    if has_value and calibrated:
        extras.append("corretta sugli errori misurati")
    if evidence is not None and evidence.external and not evidence.gate.use_external:
        extras.append("prezzi di altri mercati esclusi: peggioravano l'errore misurato")
    if (
        evidence is not None
        and not evidence.gate.use_own_purchases
        and any(p.source == "own_purchase" for p in evidence.own)
    ):
        extras.append("tuoi acquisti esclusi: peggioravano l'errore misurato")
    if extras:
        label = label.rstrip(".") + " · " + " · ".join(extras) + "."

    low = float(market.quick_sale_price) if market.quick_sale_price is not None else None
    high = float(market.optimistic_sale_price) if market.optimistic_sale_price is not None else None
    if not has_value:
        range_label = "Dati insufficienti per minimo e massimo."
    elif calibrated:
        range_label = (
            f"Minimo e massimo calibrati sugli errori misurati su {calibration_n} vendite reali "
            "(circa 8 vendite su 10 cadono nell'intervallo)."
        )
    else:
        range_label = "25° e 75° percentile dei comparabili usati."
    if new_cap_applied:
        range_label = range_label.rstrip(".") + " · massimo limitato al prezzo da nuovo."

    if days_basis == "sold":
        days_label = (
            f"Tempo di vendita di {_n(velocity_n, 'articolo simile venduto', 'articoli simili venduti')} "
            "su Vinted, affiancato al tempo tipico della categoria."
        )
    elif days_basis == "segment":
        days_label = "Media del segmento (brand e categoria): nessun articolo simile venduto osservato."
    else:
        days_label = "Tempo tipico della categoria: nessuna vendita osservata."

    p = p_sale.get("p")
    p_basis = p_sale.get("source") or "insufficient"
    if p_basis not in ("similar", "segment"):
        p_basis = "insufficient"
    if p is None:
        p_label = str(p_sale.get("reason") or "Dati insufficienti.")
    elif p_basis == "segment":
        p_label = f"Quota di venduti del segmento (brand e categoria) su {p_sale.get('n', 0)} annunci."
    else:
        p_label = (
            f"Quota di {_n(int(p_sale.get('n') or 0), 'annuncio simile', 'annunci simili')} "
            f"venduti entro {p_sale.get('horizon_days', 30)} giorni."
        )

    new_price = None
    if evidence is not None and evidence.new_prices:
        refs = evidence.new_prices
        new_price = {
            "value": round(float(evidence.new_median or 0), 2),
            "n": len(refs),
            "sources": [
                {
                    "source": r.source,
                    "price": round(r.price, 2),
                    "currency": r.currency,
                    "date": r.at.date().isoformat(),
                    "url": r.url,
                }
                for r in refs[:MAX_NEW_SOURCES]
            ],
            "label": f"Mediana di {_n(len(refs), 'prezzo da nuovo', 'prezzi da nuovo')} trovati nei negozi: "
            + (
                "limita il prezzo massimo."
                if evidence.gate.use_new_cap
                else "solo riferimento, non usato come comparabile."
            ),
        }

    used_refs = {c.item.ref_id for c in used if c.item.source in ("external_sold", "external_asking")}
    external: list[dict[str, Any]] = []
    if evidence is not None:
        refs_sorted = sorted(
            evidence.refs.values(),
            key=lambda r: (r.id not in used_refs, {"sold": 0, "asking": 1}.get(r.kind, 2), -r.at.timestamp()),
        )
        external = [r.as_dict(r.id in used_refs) for r in refs_sorted[:MAX_EXTERNAL_REFS]]

    return {
        "expected_price": {
            "value": float(market.expected_sale_price) if has_value else None,  # type: ignore[arg-type]
            "basis": basis,
            "n": market.n_used,
            "by_source": by_source,
            "negotiation_discount": round(discount, 4) if discount else None,
            "calibrated": bool(calibrated and has_value),
            "prior": {
                "used": market.used_prior,
                "level": prior.level if prior is not None else None,
                "n": prior.sample_size if prior is not None else 0,
            },
            "label": label,
        },
        "price_range": {
            "low": low,
            "high": high,
            "basis": "calibration" if calibrated and has_value else "percentiles",
            "label": range_label,
        },
        "days_to_sell": {
            "value": velocity_days,
            "n": velocity_n,
            "basis": days_basis,
            "label": days_label,
        },
        "sale_probability": {"value": p, "n": int(p_sale.get("n") or 0), "basis": p_basis, "label": p_label},
        "new_price": new_price,
        "real_sales": {
            "total": sales,
            "own": own,
            "vinted_sold": by_source["vinted_sold"],
            "external_sold": by_source["external_sold"],
        },
        "external": external,
    }
