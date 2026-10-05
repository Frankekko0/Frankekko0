"""Negotiation / Offer Engine.

Suggests prices; never sends offers. Three thresholds:

* **Maximum purchase** - meets the user's min profit and min ROI at the *expected* resale price.
* **Good purchase**    - meets the same targets even at the *quick-sale* (conservative) price.
* **Suggested first offer** - an opening offer that leaves room to negotiate, never below what
  sellers typically accept (~75% of the asking price) and never above the good-purchase price.

When the asking price is already far below the good-purchase price on a strong deal the engine
recommends buying immediately: haggling over a few euros risks losing the item to another buyer.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from app.domain.enums import RecommendedAction

MIN_OFFER_RATIO = Decimal("0.75")
OPENING_DISCOUNT = Decimal("0.85")


@dataclass(frozen=True)
class OfferPlan:
    listed_price: Decimal
    suggested_offer: Decimal | None
    good_buy_price: Decimal | None
    max_buy_price: Decimal | None
    action: RecommendedAction
    rationale: str


def _whole(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_FLOOR).quantize(Decimal("0.01"))


def build_offer_plan(
    listed_price: Decimal,
    max_buy: Decimal | None,
    good_buy: Decimal | None,
    flip_score: int,
    risk_score: int,
    market_reliable: bool = True,
) -> OfferPlan:
    floor_offer = _whole(listed_price * MIN_OFFER_RATIO)

    if not market_reliable:
        return OfferPlan(
            listed_price,
            None,
            good_buy,
            max_buy,
            RecommendedAction.WATCH,
            "Dati di mercato insufficienti: osserva l'annuncio prima di fare offerte.",
        )
    if risk_score >= 75:
        return OfferPlan(
            listed_price,
            None,
            good_buy,
            max_buy,
            RecommendedAction.SKIP,
            "Rischio molto alto: meglio non acquistare senza verifiche approfondite.",
        )
    if max_buy is None or max_buy < floor_offer:
        action = RecommendedAction.WATCH if flip_score >= 45 else RecommendedAction.SKIP
        why = (
            "Al prezzo attuale non raggiungi i tuoi obiettivi di profitto/ROI: attendi un ribasso."
            if action == RecommendedAction.WATCH
            else "Margine insufficiente anche con un'offerta: non conviene."
        )
        return OfferPlan(listed_price, None, good_buy, max_buy, action, why)

    if (
        good_buy is not None
        and listed_price <= good_buy * Decimal("0.92")
        and flip_score >= 80
        and risk_score < 50
    ):
        return OfferPlan(
            listed_price,
            listed_price,
            good_buy,
            max_buy,
            RecommendedAction.BUY_NOW,
            "Prezzo già molto sotto la soglia di buon acquisto: trattare rischia di farti perdere l'affare.",
        )

    if listed_price <= max_buy:
        target = listed_price * OPENING_DISCOUNT
        if good_buy is not None:
            target = min(target, good_buy)
        offer = max(_whole(target), floor_offer)
        if offer >= listed_price:
            return OfferPlan(
                listed_price,
                listed_price,
                good_buy,
                max_buy,
                RecommendedAction.BUY_NOW,
                "Il prezzo richiesto rientra già nei tuoi obiettivi.",
            )
        return OfferPlan(
            listed_price,
            offer,
            good_buy,
            max_buy,
            RecommendedAction.MAKE_OFFER,
            "Prezzo nei tuoi obiettivi: prova un'offerta, puoi salire fino al prezzo massimo.",
        )

    offer = max(_whole(max_buy * Decimal("0.95")), floor_offer)
    return OfferPlan(
        listed_price,
        offer,
        good_buy,
        max_buy,
        RecommendedAction.MAKE_OFFER,
        "Conviene solo con uno sconto: non superare il prezzo massimo d'acquisto.",
    )
