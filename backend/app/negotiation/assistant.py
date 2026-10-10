"""A negotiation plan for one listing: asked price, ideal offer, the most worth paying, the discount needed
to reach the target ROI, the profit at several prices, how willing the seller is likely to be, why the price
should come down, and courteous messages.

The messages are short and honest: no false urgency, no invented competing buyers, no "last price". The
reason given for a lower price is always a fact the analysis has (similar items sold lower, shipping and
fees), never pressure. Sending is left to the user (FlipFinder does not act on Vinted).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.agent.guardrails import injection_suspected, neutralise
from app.analysis.text import off_platform_hits

PRESSURE_PHRASES = (
    "ultimo prezzo",
    "altri interessati",
    "altri acquirenti",
    "subito o",
    "solo oggi",
    "devo decidere",
    "offerta valida",
    "ultima occasione",
    "affrettati",
)


@dataclass(frozen=True)
class SellerSignals:
    days_online: float | None = None
    price_drops: int = 0
    favourites: int | None = None
    views: int | None = None
    bundle_possible: bool = False  # the seller has other items worth buying together


@dataclass
class NegotiationPlan:
    asked: float
    ideal_offer: float | None
    max_acceptable: float | None
    discount_needed: float | None  # euros below the asked price to reach the target ROI
    discount_needed_pct: float | None
    profit_table: list[dict[str, float | None]] = field(default_factory=list)
    willingness: int = 50
    willingness_factors: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    messages: dict[str, str] = field(default_factory=dict)
    # Where each message comes from: {"source": "template" | "model", "violations": [codes]}. The templates are
    # the default; the model's drafts replace them one by one, only when they pass ``negotiation.guard``.
    message_meta: dict[str, dict[str, Any]] = field(default_factory=dict)
    ai: dict[str, Any] = field(default_factory=lambda: ai_block())
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "asked": self.asked,
            "ideal_offer": self.ideal_offer,
            "max_acceptable": self.max_acceptable,
            "discount_needed": self.discount_needed,
            "discount_needed_pct": self.discount_needed_pct,
            "profit_table": self.profit_table,
            "willingness": self.willingness,
            "willingness_factors": self.willingness_factors,
            "reasons": self.reasons,
            "messages": self.messages,
            "messages_meta": self.message_meta,
            "ai": self.ai,
            "note": self.note,
        }


def ai_block(**fields: Any) -> dict[str, Any]:
    """What the answer says about the model's part in the messages (nothing, until a draft is asked for)."""
    return {
        "enabled": False,  # the model can be asked to write the messages
        "used": False,  # at least one message below is the model's
        "provider": None,
        "model": None,
        "fallback": None,  # why the templates stayed: disabled | no_model | injection | rate_limited | ...
        "retry_after": None,
        "prompt_version": None,
        "tone": None,
        "cached": False,
        "generated_at": None,
        "injection_suspected": False,
        **fields,
    }


def eur(v: float) -> str:
    s = f"{v:.2f}"
    return (s[:-3] if s.endswith(".00") else s.replace(".", ",")) + " €"


def willingness(sig: SellerSignals) -> tuple[int, list[str]]:
    score, why = 40, []
    if sig.days_online is not None:
        if sig.days_online >= 14:
            score += 15
            why.append(f"Online da {sig.days_online:.0f} giorni: probabile apertura a una trattativa")
        elif sig.days_online < 2:
            score -= 15
            why.append("Pubblicato da pochissimo: di solito non scende subito")
    if sig.price_drops:
        score += 10
        why.append(f"Ha già ribassato il prezzo {sig.price_drops} volta/e")
    if sig.favourites is not None:
        if sig.favourites >= 20:
            score -= 10
            why.append(f"{sig.favourites} preferiti: c'è interesse, meno spazio per sconti")
        elif sig.favourites < 5 and (sig.days_online or 0) >= 7:
            score += 10
            why.append("Pochi preferiti dopo diversi giorni")
    if sig.bundle_possible:
        score += 10
        why.append("Altri articoli del venditore: un lotto può giustificare uno sconto")
    return max(0, min(100, score)), why


def _short(title: str, limit: int = 40) -> str:
    t = " ".join(title.split())
    if len(t) <= limit:
        return t
    cut = t[: limit - 1]
    if t[limit - 1] != " " and " " in cut:  # do not cut a word in half
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + "…"


GENERIC_ITEM = "l'articolo"
_PRICE_IN_TITLE = re.compile(
    r"(?:€|\beur(?:o|os)?\b)\s*\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\s*(?:€|eur(?:o|os)?\b)", re.IGNORECASE
)


def item_label(title: str | None, limit: int = 40) -> str:
    """The item as named in a message: the start of the listing title, made inert (no markup that could close
    the untrusted-text delimiters), without prices, and replaced by a generic word when the title carries a
    contact, a link, a payment outside the platform, an order or pressure. The title is the seller's text: only
    this label ever reaches a message, never the title itself."""
    clean = " ".join(_PRICE_IN_TITLE.sub(" ", neutralise(title or "")).split())
    if not clean or off_platform_hits(clean) or injection_suspected(clean) or pressure_phrases(clean):
        return GENERIC_ITEM
    return _short(clean, limit)


def opening_offer(suggested: float | None, max_buy: float | None) -> float | None:
    """The price to open with, always room below the most worth paying. It is the offer engine's own suggestion
    (``Opportunity.suggested_offer``); without one, a notch under the maximum (the engine's 95% rule, in whole
    euros), so the seller has something to haggle down from and the buyer something to come up to. The stored
    suggestion was made with the economics of the analysis: it is clamped to the maximum of the user asking now."""
    if suggested is not None and suggested > 0:
        return suggested if max_buy is None else min(suggested, max_buy)
    if max_buy is None or max_buy <= 0:
        return None
    lower = float(math.floor(max_buy * 0.95))
    return lower if lower > 0 else round(max_buy * 0.95, 2)


def build_plan(
    *,
    title: str,
    asked: float,
    profit_at: Callable[[float], tuple[float, float | None]],  # price -> (profit, roi)
    max_buy: float | None,
    ideal_offer: float | None,
    similar_sold_median: float | None = None,
    signals: SellerSignals | None = None,
    marketplace_fees_note: bool = True,
) -> NegotiationPlan:
    sig = signals or SellerSignals()
    score, factors = willingness(sig)
    if ideal_offer is not None and max_buy is not None:
        ideal_offer = min(ideal_offer, max_buy)  # the opening offer is never above what is worth paying
    plan = NegotiationPlan(
        asked=asked,
        ideal_offer=ideal_offer,
        max_acceptable=max_buy,
        discount_needed=None,
        discount_needed_pct=None,
        willingness=score,
        willingness_factors=factors,
    )
    if max_buy is not None and asked > max_buy:
        plan.discount_needed = round(asked - max_buy, 2)
        plan.discount_needed_pct = round((asked - max_buy) / asked, 4)
    prices = {
        round(asked, 2),
        round(asked * 0.95, 2),
        round(asked * 0.90, 2),
        round(asked * 0.85, 2),
        round(asked * 0.80, 2),
    }
    if max_buy is not None and max_buy > 0:
        prices.add(round(max_buy, 2))
    for p in sorted(prices, reverse=True):
        profit, roi = profit_at(p)
        plan.profit_table.append(
            {"price": p, "profit": round(profit, 2), "roi": None if roi is None else round(roi, 4)}
        )
    if similar_sold_median is not None and similar_sold_median < asked * 0.98:
        plan.reasons.append(f"Articoli simili sono stati venduti intorno a {eur(similar_sold_median)}")
    if marketplace_fees_note:
        plan.reasons.append(
            "Al prezzo richiesto, tra protezione acquirenti e spedizione, il margine per me non regge"
        )
    short = item_label(title)
    offer = ideal_offer if ideal_offer is not None else max_buy
    if offer is None or offer >= asked:
        plan.note = "Il prezzo richiesto è già dentro la soglia: non serve trattare, salvo un lotto."
        plan.messages = {"accept": f"Ciao, mi interessa {short}: la prendo al prezzo indicato. Grazie!"}
    else:
        why = (
            f" Ho visto che articoli simili sono venduti intorno a {eur(similar_sold_median)}."
            if similar_sold_median and similar_sold_median < asked
            else ""
        )
        plan.messages = {
            "first_offer": f"Ciao, mi interesserebbe {short}: potresti valutare {eur(offer)} per concludere l'acquisto?{why} Grazie!",
            "counter_reply": (
                f"Grazie della risposta! Con {eur(max_buy)} riuscirei a concludere subito."
                if max_buy is not None
                else "Grazie della risposta! Ci penso e ti faccio sapere."
            ),
            "accept": "Perfetto, grazie! Procedo con l'acquisto.",
            "decline_politely": "Grazie lo stesso, a quel prezzo non riesco. Se cambi idea fammi sapere!",
        }
        if sig.bundle_possible:
            plan.messages["bundle"] = (
                "Ciao, ho visto che hai anche altri articoli che mi interessano: se ne prendo più di uno "
                "potresti farmi un prezzo complessivo? Grazie!"
            )
        if max_buy is not None and asked <= max_buy:
            plan.note = "Il prezzo richiesto è già dentro la soglia: una piccola offerta è facoltativa, si può anche accettare."
            plan.messages["accept"] = f"Ciao, mi interessa {short}: la prendo al prezzo indicato. Grazie!"
        elif max_buy is not None and max_buy < asked * 0.75:
            plan.note = "Lo sconto necessario supera il 25%: è probabile che il venditore non accetti. Meglio osservare."
    plan.message_meta = {k: {"source": "template", "violations": []} for k in plan.messages}
    return plan


def pressure_phrases(text: str) -> list[str]:
    low = text.lower()
    return [p for p in PRESSURE_PHRASES if p in low]
