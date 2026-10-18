"""Market Price Engine: Fair Market Value and realistic resale scenarios.

Pipeline (pure function ``estimate_market_value``):

1. *one tier only*: when the subject has a model (or a distinctive line) the price is built from the
   comparables of that model/line and nothing else; the rest of the brand (another line, another
   height of the shoe) is kept as a labelled reference. Fewer than 3 usable comparables of the
   subject's own model/line means **no estimate** ("dati insufficienti"), never a brand-wide number
   passed off as the item's value. A subject with nothing distinctive gets a brand-and-category
   estimate, labelled and capped (``FAMILY_CONFIDENCE_CAP``);
2. price levels and outliers inside that tier, separately for sold and active comparables: a gap
   between price levels (another season, a price drop) picks the level with the most weight and marks
   the estimate *mixed* (``app.pricing.stats.cluster_prices``); active asks not seen for
   ``ACTIVE_FRESH_DAYS`` have an unknown fate and set no price;
3. "realized" distribution = sold prices (full weight) + active asking prices discounted by the
   ask-to-sale ratio (asks are systematically higher than what items actually sell for) with a
   reduced weight that shrinks as sold evidence grows. With enough sales the asks are reference
   only; when the fresh asks sit below the sales the market has moved and the asks stay in;
4. a falling market (the recent sales clearly below the older ones, or fresh asks below the sales) moves
   the estimate to the recent level and is declared; the segment prior never moves the median (it only
   widens the interval of a thin estimate when it is the same model's own sold history);
5. FMV = weighted median of the realized distribution; scenarios: quick = P25,
   expected = FMV, optimistic = P75 (capped at P90);
6. market confidence from sample size, sold share, similarity, dispersion and recency, scaled down
   by the age of the evidence and capped when the levels are mixed or the basis is brand-wide.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any

from app.pricing.comparables import (
    CONDITION_MULTIPLIER,
    REASON_OTHER_LINE,
    TIER_FAMILY,
    TIER_LINE,
    TIER_MODEL,
    ItemProfile,
    ScoredComparable,
    subject_kind,
)
from app.pricing.stats import DistributionStats, WeightedValue, cluster_prices, describe, histogram

DEFAULT_ASK_TO_SALE = 0.88
MIN_COMPARABLES = 3
SOLD_ONLY_MIN = 5

# An active ask not seen for this long may have sold, dropped or vanished: it sets no price (it still
# feeds demand elsewhere).
ACTIVE_FRESH_DAYS = 21
# Fresh asks are normally ABOVE the sales (sellers ask more than items fetch: median ask / median sold is about
# 1.14). Fresh asks below the sales mean the market moved down: they stay in the price and the estimate follows them.
ASKS_BELOW_RATIO = 0.90
# Recent sales this far below the older ones (and clearly more than noise) declare a falling market.
TREND_DROP = 0.85
TREND_RECENT_DAYS = 30
TREND_MIN_SIDE = 3
TREND_MIN_Z = 2.0
# A brand-and-category estimate (nothing distinctive about the item) is never more than "indicative".
FAMILY_CONFIDENCE_CAP = 35
MIXED_CONFIDENCE_CAP = 45

REASON_OUTLIER = "outlier"
REASON_ASKING = "asking_price"
REASON_OTHER_LEVEL = "other_price_level"
REASON_STALE_ASK = "stale_ask"


@dataclass(frozen=True)
class SegmentPrior:
    """Aggregated statistics of the product segment (from the Market Database)."""

    median_price: float
    p25_price: float
    p75_price: float
    sample_size: int
    ask_to_sale_ratio: float | None = None
    sell_through_rate: float | None = None
    avg_days_to_sale: float | None = None
    # Which segment answered: "model" (brand x category x model) or "brand_category".
    level: str | None = None
    # Concluded sales behind the segment's prices (None: not known). Prices of a segment with no sale at all
    # are asks of items that may never have sold: not a reference.
    sold_count: int | None = None

    @property
    def has_sales(self) -> bool:
        return self.sold_count is None or self.sold_count >= MIN_COMPARABLES


@dataclass
class MarketEstimate:
    fair_market_value: Decimal | None
    quick_sale_price: Decimal | None
    expected_sale_price: Decimal | None
    optimistic_sale_price: Decimal | None
    stats: DistributionStats | None
    sold_stats: DistributionStats | None
    active_stats: DistributionStats | None
    n_used: int
    n_sold: int
    n_active: int
    n_outliers: int
    avg_similarity: float
    ask_to_sale_ratio: float
    confidence: int
    used_prior: bool
    notes: list[str] = field(default_factory=list)
    histogram: list[dict[str, float]] = field(default_factory=list)
    comparables: list[ScoredComparable] = field(default_factory=list)
    confidence_breakdown: dict[str, float] = field(default_factory=dict)
    # Which comparables the price comes from: "model" | "line" | "family" (brand and category only); None when
    # the estimate was made without a subject (one undifferentiated pool).
    tier: str | None = None
    # Comparables of the subject's own model/line that were used (0 for a brand-and-category estimate).
    n_specific: int = 0
    # More than one price level among the comparables (the most represented one was used).
    mixed: bool = False
    # "falling" | "rising": recent sales clearly away from the older ones (falling moves the estimate).
    trend: str | None = None
    median_age_days: float | None = None
    # The rest of the brand/category (other lines), shown as a reference and never part of the price.
    reference: dict[str, Any] | None = None
    # Why there is no estimate, in words (when ``fair_market_value`` is None).
    insufficient_reason: str | None = None

    @property
    def has_value(self) -> bool:
        return self.fair_market_value is not None

    def snapshot(self) -> dict[str, Any]:
        def s(st: DistributionStats | None) -> dict[str, float] | None:
            if st is None:
                return None
            return {k: round(v, 2) if isinstance(v, float) else v for k, v in st.__dict__.items()}

        return {
            "stats": s(self.stats),
            "sold_stats": s(self.sold_stats),
            "active_stats": s(self.active_stats),
            "n_used": self.n_used,
            "n_sold": self.n_sold,
            "n_active": self.n_active,
            "n_outliers": self.n_outliers,
            "avg_similarity": round(self.avg_similarity, 3),
            "ask_to_sale_ratio": round(self.ask_to_sale_ratio, 3),
            "confidence": self.confidence,
            "confidence_breakdown": {k: round(v, 3) for k, v in self.confidence_breakdown.items()},
            "used_prior": self.used_prior,
            "notes": self.notes,
            "histogram": self.histogram,
            "tier": self.tier,
            "n_specific": self.n_specific,
            "mixed": self.mixed,
            "trend": self.trend,
            "median_age_days": round(self.median_age_days, 1) if self.median_age_days is not None else None,
            "reference": self.reference,
        }


def _euros(value: float, mode: str = "round") -> Decimal:
    rounding = ROUND_FLOOR if mode == "floor" else ROUND_HALF_UP
    return Decimal(str(value)).quantize(Decimal("1"), rounding=rounding).quantize(Decimal("0.01"))


def _age_days(c: ScoredComparable, now: datetime) -> float:
    observed = c.item.observed_at()
    return max(0.0, (now - observed).total_seconds() / 86400) if observed else 0.0


def _level(group: list[ScoredComparable]) -> tuple[list[ScoredComparable], bool, int]:
    """The price level of a group: (comparables kept, mixed, outliers dropped). Everything else is marked
    as excluded, with the reason."""
    if not group:
        return [], False, 0
    res = cluster_prices([c.adjusted_price for c in group], [c.weight for c in group])
    for i in res.other:
        group[i].included = False
        group[i].exclusion_reason = REASON_OTHER_LEVEL
    for i in res.outliers:
        group[i].included = False
        group[i].exclusion_reason = REASON_OUTLIER
    return [group[i] for i in res.kept], res.mixed, len(res.outliers)


def _reference(rest: list[ScoredComparable]) -> dict[str, Any] | None:
    """The comparables outside the subject's tier, as a reference: counts and medians, nothing else."""
    if not rest:
        return None
    sold = [c.adjusted_price for c in rest if c.is_sold]
    asks = [c.adjusted_price for c in rest if not c.is_sold]
    return {
        "n": len(rest),
        "n_sold": len(sold),
        "median_sold": round(statistics.median(sold), 2) if sold else None,
        "median_ask": round(statistics.median(asks), 2) if asks else None,
        "label": "Altre linee o versioni dello stesso brand: riferimento, non usato per il prezzo.",
    }


def _trend(sold: list[ScoredComparable], median: float, now: datetime) -> tuple[str | None, float | None]:
    """("falling" | "rising" | None, recent median): the sales of the last ``TREND_RECENT_DAYS`` against the
    older ones, only when both sides are big enough and the gap is well beyond the noise of the prices."""
    recent = [c for c in sold if _age_days(c, now) <= TREND_RECENT_DAYS]
    older = [c for c in sold if _age_days(c, now) > TREND_RECENT_DAYS]
    if len(recent) < TREND_MIN_SIDE or len(older) < TREND_MIN_SIDE or median <= 0:
        return None, None
    r = [math.log(c.adjusted_price) for c in recent]
    o = [math.log(c.adjusted_price) for c in older]
    mr, mo = statistics.median(r), statistics.median(o)
    # The noise of one price: spread inside each side (not across them, which is the effect to be tested).
    mad = statistics.median([abs(v - mr) for v in r] + [abs(v - mo) for v in o])
    sigma = max(1.4826 * mad, 0.08)
    z = (mr - mo) / (sigma * math.sqrt(1 / len(r) + 1 / len(o)))
    recent_median = math.exp(mr)
    if mr - mo <= math.log(TREND_DROP) and z <= -TREND_MIN_Z:
        return "falling", recent_median
    if mr - mo >= -math.log(TREND_DROP) and z >= TREND_MIN_Z:
        return "rising", recent_median
    return None, None


def estimate_market_value(
    comparables: list[ScoredComparable],
    subject_condition: str,
    now: datetime,
    prior: SegmentPrior | None = None,
    sold_only_min: int | None = SOLD_ONLY_MIN,
    subject: ItemProfile | None = None,
) -> MarketEstimate:
    """``subject``: when given, the comparables are tiered (see the module docstring); without it the
    whole list is one pool (older callers, retroactive checks of the previous estimator)."""
    kind = subject_kind(subject) if subject is not None else None
    notes: list[str] = []
    if kind in (TIER_MODEL, TIER_LINE):
        primary = [c for c in comparables if c.tier == kind]
        rest = [c for c in comparables if c.tier != kind]
        for c in rest:
            c.included = False
            c.exclusion_reason = REASON_OTHER_LINE
    else:
        primary, rest = list(comparables), []
    reference = _reference(rest)
    family_level = kind == TIER_FAMILY
    n_primary = len(primary)

    def stats_of(group: list[ScoredComparable]) -> DistributionStats | None:
        return describe([WeightedValue(c.adjusted_price, c.weight) for c in group]) if group else None

    # ---- active asks not seen for a while have an unknown fate ------------------------------------
    sold = [c for c in primary if c.is_sold]
    active = [c for c in primary if not c.is_sold]
    stale = [c for c in active if _age_days(c, now) > ACTIVE_FRESH_DAYS]
    for c in stale:
        c.included = False
        c.exclusion_reason = REASON_STALE_ASK
    if stale:
        gone = {id(c) for c in stale}
        active = [c for c in active if id(c) not in gone]
        notes.append(
            f"{len(stale)} prezzi richiesti non rivisti da oltre {ACTIVE_FRESH_DAYS} giorni: non usati "
            "(potrebbero essere già venduti o ritirati)."
        )

    sold_in, sold_mixed, sold_out = _level(sold)
    active_in, active_mixed, active_out = _level(active)
    n_outliers = sold_out + active_out
    sold_stats, active_stats = stats_of(sold_in), stats_of(active_in)

    # Ask-to-sale ratio: how much below asking prices items actually sell (never above 1: an ask is a ceiling).
    if sold_stats and active_stats and sold_stats.n >= 3 and active_stats.n >= 3:
        k_ask = min(1.0, max(0.6, sold_stats.median / active_stats.median))
    elif prior and prior.ask_to_sale_ratio:
        k_ask = min(1.0, prior.ask_to_sale_ratio)
    else:
        k_ask = DEFAULT_ASK_TO_SALE

    # Fresh asks below the sales: the market moved down, whatever the age of the sales (items listed before the
    # drop keep selling at the old price for weeks). The asks, discounted as usual, are then the newer evidence.
    asks_level: float | None = None
    if sold_stats and active_stats and len(active_in) >= TREND_MIN_SIDE:
        # The typical ask below the sales and three quarters of the asks below them (a few bargains among
        # ordinary asks are not a falling market).
        if active_stats.median < sold_stats.median * ASKS_BELOW_RATIO and active_stats.p75 < sold_stats.median:
            asks_level = active_stats.median * DEFAULT_ASK_TO_SALE
            notes.append(
                "I prezzi richiesti recenti sono più bassi delle vendite: mercato in calo, stima allineata agli annunci "
                "attuali."
            )

    # Enough real sales: asking prices are shown for reference but do not set the price (they
    # overstate it; measured on past sales) - unless the market is falling (above).
    if sold_only_min is not None and len(sold_in) >= sold_only_min and asks_level is None:
        for c in active_in:
            c.included = False
            c.exclusion_reason = REASON_ASKING
        active_in = []
    # Price levels matter only where the group sets the price: asks left out as reference may be as mixed as they like.
    mixed = sold_mixed or (active_mixed and bool(active_in))
    active_factor = 0.7 if len(sold_in) < 5 else 0.35
    realized: list[WeightedValue] = [WeightedValue(c.adjusted_price, c.weight) for c in sold_in]
    realized += [WeightedValue(c.adjusted_price * k_ask, c.weight * active_factor) for c in active_in]
    used = sold_in + active_in
    n_used = len(used)
    avg_sim = sum(c.similarity for c in used) / n_used if used else 0.0

    if n_used < MIN_COMPARABLES:
        notes.append("Non ci sono abbastanza dati per stimare con affidabilità il prezzo di mercato.")
        reason: str | None = None
        if kind in (TIER_MODEL, TIER_LINE):
            what = f"dello stesso modello ({subject.model})" if kind == TIER_MODEL and subject else "della stessa linea"
            reason = (
                f"Nessun confronto {what}: 0 comparabili utilizzabili"
                if n_used == 0
                else f"Solo {n_used} comparabili {what} utilizzabili"
            ) + ", dati insufficienti per stimare il valore. Nessun punteggio assegnato."
            notes.append(reason)
            if reference:
                notes.append(
                    f"Altre linee dello stesso brand: {reference['n']} annunci"
                    + (f", mediana venduti €{reference['median_sold']:.0f}" if reference["median_sold"] else "")
                    + ". Riferimento non usato come stima."
                )
        elif kind == TIER_FAMILY and subject is not None and not subject.category:
            reason = (
                "Categoria non riconosciuta e nessun nome di modello nel titolo: nessun confronto affidabile. "
                "Nessun punteggio assegnato."
            )
            notes.append(reason)
        elif prior and prior.sample_size >= 5 and prior.has_sales:
            notes.append(
                f"Riferimento di segmento (brand e categoria): mediana €{prior.median_price:.0f} su "
                f"{prior.sample_size} annunci. Solo indicativo, non usato come stima."
            )
        return MarketEstimate(
            fair_market_value=None,
            quick_sale_price=None,
            expected_sale_price=None,
            optimistic_sale_price=None,
            stats=None,
            sold_stats=sold_stats,
            active_stats=active_stats,
            n_used=n_used,
            n_sold=len(sold_in),
            n_active=len(active_in),
            n_outliers=n_outliers,
            avg_similarity=avg_sim,
            ask_to_sale_ratio=k_ask,
            confidence=0,
            used_prior=False,
            notes=notes,
            histogram=[],
            comparables=comparables,
            tier=kind,
            n_specific=n_used if not family_level else 0,
            mixed=mixed,
            reference=reference,
            insufficient_reason=reason,
        )

    used_prior = False
    stats = describe(realized)
    median, p25, p75, p90, max_r = stats.median, stats.p25, stats.p75, stats.p90, stats.max_reasonable

    # ---- a falling market: the estimate follows the recent sales -----------------------------------
    trend, recent_median = _trend(sold_in, median, now)
    if asks_level is not None:
        trend = "falling"
        recent_median = min(recent_median, asks_level) if recent_median else asks_level
    if trend == "falling":
        factor = min(1.0, recent_median / median) if recent_median else 1.0
        median, p25, p75, p90, max_r = (v * factor for v in (median, p25, p75, p90, max_r))
        notes.append("Prezzi in calo: stima allineata ai prezzi più recenti.")
    elif trend == "rising":
        notes.append("Prezzi in aumento: stima prudente, non allineata ai rialzi recenti.")

    # The segment prior never moves the median. Only the same model's own sold history widens the
    # interval of a thin estimate (it can say the spread is bigger than a few comparables show).
    if (
        prior
        and prior.level == "model"
        and prior.sample_size >= 5
        and prior.has_sales
        and n_used < 10
        and not family_level
    ):
        cond_mult = CONDITION_MULTIPLIER.get(subject_condition, 0.95)
        w_direct = n_used / (n_used + 3.0)
        p25 = min(p25, w_direct * p25 + (1 - w_direct) * prior.p25_price * cond_mult)
        p75 = max(p75, w_direct * p75 + (1 - w_direct) * prior.p75_price * cond_mult)
        used_prior = True
        notes.append("Pochi comparabili diretti: intervallo affiancato alle statistiche del modello.")

    quick = min(p25, median * 0.93)
    optimistic = max(p75, median * 1.07)
    optimistic = min(optimistic, max(p90, median * 1.07), max(max_r, median * 1.07))

    # ---- confidence -------------------------------------------------------------------------
    sample_f = 1 - math.exp(-n_used / 10)
    sold_f = min(1.0, len(sold_in) / 8)
    sim_f = min(1.0, max(0.0, (avg_sim - 0.4) / 0.6)) if used else 0.0
    dispersion = stats.dispersion
    disp_f = 1 / (1 + 2 * dispersion)
    ages = [_age_days(c, now) for c in used]
    recent_n = sum(1 for a in ages if a <= 45)
    recency_f = 0.6 + 0.4 * (recent_n / n_used) if used else 0.6
    median_age = statistics.median(ages) if ages else None
    # Old evidence is old whatever the rest says: 45 days or less costs nothing, a quarter of a year half.
    age_f = 1.0 if median_age is None else max(0.5, 1 - max(0.0, median_age - 45) / 150)
    breakdown = {
        "sample": sample_f,
        "sold": sold_f,
        "similarity": sim_f,
        "dispersion": disp_f,
        "recency": recency_f,
        "age": age_f,
    }
    confidence = 100 * (0.35 * sample_f + 0.20 * sold_f + 0.20 * sim_f + 0.15 * disp_f + 0.10 * recency_f) * age_f
    if family_level:
        confidence = min(confidence, FAMILY_CONFIDENCE_CAP)
        notes.append(
            "Nessun confronto dello stesso modello o linea: stima a livello di brand e categoria, solo indicativa."
        )
    if mixed or trend == "falling":
        confidence = min(confidence, MIXED_CONFIDENCE_CAP)
    if mixed:
        notes.append(
            "Prezzi su livelli diversi (altra versione, stagione o mercato cambiato): usato il livello più "
            "rappresentato."
        )
    if reference and not family_level:
        notes.append(
            f"Altre linee dello stesso brand ({reference['n']} annunci) non usate per il prezzo: solo riferimento."
        )
    if n_used < 5:
        notes.append(f"Solo {n_used} comparabili utilizzabili: stima indicativa.")
    if dispersion > 0.6:
        notes.append("Prezzi di mercato molto dispersi: la stima è meno affidabile.")
    if not sold_in:
        notes.append("Nessuna vendita reale osservata: stima basata su prezzi richiesti scontati.")
    if n_outliers:
        notes.append(f"{n_outliers} prezzi anomali esclusi dal calcolo.")

    return MarketEstimate(
        fair_market_value=_euros(median),
        quick_sale_price=_euros(quick, "floor"),
        expected_sale_price=_euros(median),
        optimistic_sale_price=_euros(optimistic),
        stats=stats,
        sold_stats=sold_stats,
        active_stats=active_stats,
        n_used=n_used,
        n_sold=len(sold_in),
        n_active=len(active_in),
        n_outliers=n_outliers,
        avg_similarity=avg_sim,
        ask_to_sale_ratio=k_ask,
        confidence=round(confidence),
        used_prior=used_prior,
        notes=notes,
        histogram=histogram([c.adjusted_price for c in used]),
        comparables=comparables,
        confidence_breakdown=breakdown,
        tier=kind,
        n_specific=0 if family_level else n_used,
        mixed=mixed,
        trend=trend,
        median_age_days=median_age,
        reference=reference,
    )
