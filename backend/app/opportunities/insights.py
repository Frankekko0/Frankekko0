"""Decision insights of one analysis (pure functions).

* realistic resale (minimum / probable / maximum), net margin, time to sell, maximum price and
  suggested offer, each with its confidence;
* probability of selling within the horizon and probability of authenticity;
* **risk-adjusted expected profit** = net margin x P(sale) x P(authentic): the ranking key;
* score breakdown (margin, demand, risk, seller) and a three-line reason;
* demand (favourites per day, age, price drops, seasonality, demand for the size and the colour,
  selling speed of similar items the user tracked), seller (reviews, seniority, activity, habit of
  lowering prices), identification (hidden opportunities) and declared vs photographed condition.

Every figure that the data cannot support is ``None`` with a reason ("dati insufficienti"),
never a made-up number.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from itertools import pairwise
from typing import Any

from app.pricing.comparables import ScoredComparable

HORIZON_DAYS = 30
MIN_OUTCOMES = 6
MONTHS = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]
CONDITION_ORDER = ["new_with_tags", "new_without_tags", "very_good", "good", "satisfactory"]
CONDITION_LABELS = {
    "new_with_tags": "nuovo con cartellino",
    "new_without_tags": "nuovo senza cartellino",
    "very_good": "ottime condizioni",
    "good": "buone condizioni",
    "satisfactory": "discrete condizioni",
    "unknown": "non indicata",
}


@dataclass
class InsightInput:
    now: datetime
    price: Decimal
    favourites: int
    listing_age_hours: float | None
    size: str | None
    color: str | None
    condition_declared: str
    condition_effective: str
    price_history: list[tuple[datetime, Decimal]] = field(default_factory=list)
    seller_rating: float | None = None
    seller_reviews: int | None = None
    seller_habits: dict[str, Any] | None = None
    identification: dict[str, Any] = field(default_factory=dict)
    vision: dict[str, Any] | None = None


def _f(v: Any) -> float | None:
    return float(v) if v is not None else None


# ------------------------------------------------------------------ probability of sale
def outcome_of(c: ScoredComparable, now: datetime, horizon: int = HORIZON_DAYS) -> bool | None:
    """True: sold within ``horizon`` days of publication; False: not; None: too early to tell."""
    it = c.item
    pub = it.published_at
    if pub is None:
        return None
    if it.status == "sold" and it.sold_at is not None:
        return (it.sold_at - pub).total_seconds() / 86400 <= horizon
    if it.status == "removed":
        return False
    if it.status == "active":
        return False if (now - pub).total_seconds() / 86400 > horizon else None
    return None


def probability_of_sale(
    similar: list[ScoredComparable], now: datetime, horizon: int = HORIZON_DAYS
) -> dict[str, Any]:
    """Weighted share of similar listings that sold within the horizon (Laplace smoothed)."""
    succ = fail = 0.0
    n = 0
    for c in similar:
        o = outcome_of(c, now, horizon)
        if o is None:
            continue
        n += 1
        if o:
            succ += c.weight
        else:
            fail += c.weight
    if n < MIN_OUTCOMES or succ + fail <= 0:
        return {
            "p": None,
            "n": n,
            "horizon_days": horizon,
            "reason": f"Solo {n} annunci simili con esito noto.",
        }
    share = succ / (succ + fail)
    p = (share * n + 1) / (n + 2)
    return {"p": round(p, 3), "n": n, "horizon_days": horizon}


def _sell_share(items: list[ScoredComparable], now: datetime) -> tuple[float | None, int]:
    outcomes = [o for c in items if (o := outcome_of(c, now)) is not None]
    if len(outcomes) < MIN_OUTCOMES:
        return None, len(outcomes)
    return sum(outcomes) / len(outcomes), len(outcomes)


# ------------------------------------------------------------------ demand
def demand_detail(
    inp: InsightInput, similar: list[ScoredComparable], tracked_ids: set[Any]
) -> dict[str, Any]:
    age_days = inp.listing_age_hours / 24 if inp.listing_age_hours is not None else None
    fav_day = round(inp.favourites / max(age_days, 0.5), 2) if age_days is not None else None

    hist = sorted(inp.price_history, key=lambda x: x[0])
    drops = [(b[0], float(a[1]), float(b[1])) for a, b in pairwise(hist) if b[1] < a[1]]
    first = float(hist[0][1]) if hist else float(inp.price)
    drop_total = (first - float(inp.price)) / first if first > 0 and float(inp.price) < first else 0.0

    overall, n_all = _sell_share(similar, inp.now)
    size_items = [c for c in similar if inp.size and c.item.size == inp.size]
    color_items = [c for c in similar if inp.color and c.item.color == inp.color]
    size_share, n_size = _sell_share(size_items, inp.now)
    color_share, n_color = _sell_share(color_items, inp.now)

    # Seasonality: sales of similar items by month, only with a year of history.
    sold = [c.item.sold_at for c in similar if c.item.status == "sold" and c.item.sold_at]
    season: dict[str, Any] = {
        "available": False,
        "reason": "Servono almeno 12 mesi di vendite di articoli simili.",
    }
    if sold and (max(sold) - min(sold)).days >= 330 and len(sold) >= 36:
        counts = [0] * 12
        for d in sold:
            counts[d.month - 1] += 1
        avg = sum(counts) / 12
        m = inp.now.month - 1
        season = {
            "available": True,
            "month": MONTHS[m],
            "factor": round(counts[m] / avg, 2) if avg else None,
            "best_months": [MONTHS[i] for i in sorted(range(12), key=lambda i: -counts[i])[:3]],
        }

    tracked = [
        (c.item.sold_at - c.item.published_at).total_seconds() / 86400
        for c in similar
        if c.item.id in tracked_ids and c.item.status == "sold" and c.item.sold_at and c.item.published_at
    ]
    return {
        "favourites_per_day": fav_day,
        "listing_age_days": round(age_days, 1) if age_days is not None else None,
        "price_drops": {
            "count": len(drops),
            "total_pct": round(drop_total, 3),
            "last_at": drops[-1][0].isoformat() if drops else None,
        },
        "sell_share": {"overall": _r(overall), "n": n_all},
        "size": {"size": inp.size, "sell_share": _r(size_share), "n": n_size},
        "color": {"color": inp.color, "sell_share": _r(color_share), "n": n_color},
        "seasonality": season,
        "tracked_similar": {
            "n": len(tracked),
            "median_days_to_sell": round(statistics.median(tracked), 1) if tracked else None,
        },
    }


def _r(v: float | None, nd: int = 3) -> float | None:
    return round(v, nd) if v is not None else None


# ------------------------------------------------------------------ seller
def seller_detail(inp: InsightInput) -> dict[str, Any]:
    habits = inp.seller_habits or {}
    return {
        "rating": inp.seller_rating,
        "reviews": inp.seller_reviews,
        # Vinted does not show the response time on the item page: never estimated.
        "response_time": None,
        "lowers_prices": {
            "listings_seen": habits.get("listings", 0),
            "with_drops": habits.get("with_drops", 0),
            "share": _r(habits["with_drops"] / habits["listings"]) if habits.get("listings") else None,
            "avg_drop_pct": _r(habits.get("avg_drop_pct")),
            "sold_after_drop": habits.get("sold_after_drop", 0),
        },
    }


# ------------------------------------------------------------------ condition
def condition_check(inp: InsightInput) -> dict[str, Any]:
    vision = inp.vision or {}
    defects = [d for d in vision.get("defects", []) if d.get("certainty") in ("certain", "probable")]
    out: dict[str, Any] = {
        "declared": inp.condition_declared,
        "declared_label": CONDITION_LABELS.get(inp.condition_declared, inp.condition_declared),
        "effective": inp.condition_effective,
        "photos_checked": bool(vision) and vision.get("analyzer") not in (None, "heuristic"),
        "defects": [
            {
                "kind": d.get("kind"),
                "severity": d.get("severity"),
                "certainty": d.get("certainty"),
                "description": d.get("description"),
            }
            for d in defects
        ],
        "differences": [],
    }
    if inp.condition_effective != inp.condition_declared:
        out["differences"].append(
            f"Dichiarato '{CONDITION_LABELS.get(inp.condition_declared, inp.condition_declared)}', dalle foto "
            f"'{CONDITION_LABELS.get(inp.condition_effective, inp.condition_effective)}': prezzi stimati su quest'ultima."
        )
    elif defects and inp.condition_declared in ("new_with_tags", "new_without_tags", "very_good"):
        out["differences"].append("Difetti visibili nelle foto non coerenti con la condizione dichiarata.")
    return out


def effective_condition(declared: str, vision: dict[str, Any] | None) -> str:
    """The worse of the declared condition and what the photos show (certain/probable evidence)."""
    if not vision:
        return declared
    from app.ingestion.normalizer import normalize_condition

    seen: str | None = None
    est = vision.get("condition_estimate") or {}
    if est.get("certainty") in ("certain", "probable") and est.get("value"):
        seen = normalize_condition(str(est["value"]))
    severe = [d for d in vision.get("defects", []) if d.get("certainty") in ("certain", "probable")]
    rank = {c: i for i, c in enumerate(CONDITION_ORDER)}
    worst = rank.get(declared)
    if worst is None:
        return declared
    if seen in rank and rank[seen] > worst:
        worst = rank[seen]
    if any(d.get("severity") == "severe" for d in severe):
        worst = max(worst, rank["satisfactory"])
    elif any(d.get("severity") == "moderate" for d in severe):
        worst = max(worst, rank["good"])
    return CONDITION_ORDER[worst]


# ------------------------------------------------------------------ identification
def identification_detail(ident: dict[str, Any]) -> dict[str, Any]:
    flags = ident.get("flags") or {}

    def val(key: str) -> Any:
        a = ident.get(key) or {}
        return a.get("value") if isinstance(a, dict) else None

    hidden = []
    if mb := flags.get("misspelled_brand"):
        hidden.append(
            f"Brand scritto male ('{mb['written']}' invece di {mb['brand']}): meno concorrenza all'acquisto."
        )
    if cm := flags.get("category_mismatch"):
        hidden.append(f"In categoria '{cm['declared']}' ma è '{cm['detected']}': meno visibile a chi cerca.")
    return {
        "brand": (ident.get("brand") or {}).get("name"),
        "line": val("line"),
        "model": val("model"),
        "category": val("category"),
        "season": val("season"),
        "product_code": val("product_code"),
        "original_price_claimed": flags.get("claimed_original_price"),
        # No reliable public source of list prices and release periods: shown only when known.
        "original_price_list": None,
        "hidden_opportunities": hidden,
        "confidence": ident.get("confidence"),
    }


# ------------------------------------------------------------------ pillars and reasons
def pillars(margin: float, demand: float, risk_score: int, seller: float) -> list[dict[str, Any]]:
    return [
        {"key": "margin", "label": "Margine", "score": round(margin)},
        {"key": "demand", "label": "Domanda", "score": round(demand)},
        {"key": "risk", "label": "Sicurezza", "score": round(100 - risk_score)},
        {"key": "seller", "label": "Venditore", "score": round(seller)},
    ]


def eur(v: float | None, sign: bool = False) -> str:
    if v is None:
        return "—"
    s = f"€{abs(v):,.0f}".replace(",", ".") if abs(v) >= 100 else f"€{abs(v):.2f}".replace(".", ",")
    return ("−" if v < 0 else "+" if sign and v > 0 else "") + s


def _num(v: float, nd: int = 1) -> str:
    return f"{v:.{nd}f}".replace(".", ",")


def _pct(v: float) -> str:
    return f"{'−' if v < 0 else ''}{abs(v):.0%}"


def reason_lines(d: dict[str, Any]) -> list[str]:
    """Three lines: money, demand and time, risk and seller."""
    r = d["resale"]
    lines = []
    if r["probable"] is None:
        lines.append(
            f"Dati insufficienti per stimare la rivendita: {d.get('insufficient_reason') or 'troppi pochi comparabili'}."
        )
    else:
        lines.append(
            f"Rivendita {eur(r['low'])}–{eur(r['high'])} (probabile {eur(r['probable'])}): margine netto "
            f"{eur(d['net_margin'], True)} dopo protezione acquisti e spedizione"
            + (f", ROI {_pct(d['roi'])}." if d.get("roi") is not None else ".")
        )
    ps = d["p_sale"]
    dem = d["demand"]
    bits = []
    if d.get("days_to_sell") is not None:
        bits.append(f"si vende in ~{d['days_to_sell']:.0f} giorni")
    if ps["p"] is not None:
        bits.append(f"{ps['p']:.0%} di probabilità di vendita entro {ps['horizon_days']} giorni")
    if dem["favourites_per_day"]:
        bits.append(f"{_num(dem['favourites_per_day'])} preferiti al giorno")
    if dem["price_drops"]["count"]:
        bits.append(f"già ribassato {dem['price_drops']['count']} volte")
    lines.append((bits[0][0].upper() + "; ".join(bits)[1:] + ".") if bits else "Domanda: dati insufficienti.")
    auth = d["authenticity"]
    s = d["seller"]
    seller_txt = (
        f"venditore {_num(s['rating'])}★ su {s['reviews']} recensioni"
        if s.get("rating") is not None and s.get("reviews")
        else "venditore senza recensioni"
        if s.get("reviews") == 0
        else "venditore non noto"
    )
    if auth["verdict"] == "not_verifiable":
        auth_txt = f"Autenticità non verificabile dalle foto (stima da prezzo, testo e venditore {auth['p_authentic']:.0%})"
    else:
        auth_txt = f"Autenticità: {auth['label'].lower()} ({auth['p_authentic']:.0%})"
    lines.append(f"{auth_txt}; {seller_txt}.")
    return lines


def risk_adjusted_profit(
    net_margin: float | None, p_sale: float | None, p_auth: float | None
) -> float | None:
    if net_margin is None or p_sale is None or p_auth is None:
        return None
    if net_margin <= 0:
        return round(net_margin, 2)  # a loss stays a loss, whatever the odds
    return round(net_margin * p_sale * p_auth, 2)
