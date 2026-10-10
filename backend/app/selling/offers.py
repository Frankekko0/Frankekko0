"""Evaluating an offer from a buyer: accept, counter or decline, with a reason and a polite reply.

An accepted offer is a sure sale now; waiting for the asking price is a chance of a better price later,
at the cost of tying the capital up. The offer is accepted when it is at least what waiting is worth:

    value of waiting = profit(asking) * P(sold within the horizon at the asking price)
                       - cost * daily capital cost * expected days to sell at the asking price

and never when it is below the floor (the minimum profit). Between the floor and the value of waiting the
reply is a counter-offer. Nothing here pressures the buyer: the replies are short and courteous.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.intelligence import survival

HORIZON_DAYS = 30.0
DAILY_CAPITAL_COST = 0.002  # 0.2% of the capital per day tied up (configurable assumption)


@dataclass(frozen=True)
class OfferDecision:
    action: str  # accept | counter | decline
    counter_price: float | None
    profit_at_offer: float
    value_of_waiting: float
    reason: str
    reply: str

    def as_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "counter_price": self.counter_price,
            "profit_at_offer": round(self.profit_at_offer, 2),
            "value_of_waiting": round(self.value_of_waiting, 2),
            "reason": self.reason,
            "reply": self.reply,
        }


def _eur(v: float) -> str:
    return f"{v:.2f}".replace(".", ",") + " €" if v % 1 else f"{int(v)} €"


def value_of_waiting(
    asking: float,
    cost: float,
    reference: float,
    profit_at: Callable[[float], float],
    fit: survival.SurvivalFit,
    daily_capital_cost: float = DAILY_CAPITAL_COST,
) -> float:
    ratio = asking / reference
    p = survival.p_sold_by(fit, ratio, HORIZON_DAYS)
    days = survival.expected_days(fit, ratio)
    return profit_at(asking) * p - cost * daily_capital_cost * days


def evaluate_offer(
    *,
    offer: float,
    asking: float,
    cost: float,
    floor: float,
    reference: float,
    profit_at: Callable[[float], float],
    fit: survival.SurvivalFit,
    buyer_name: str | None = None,
) -> OfferDecision:
    hello = f"Ciao{' ' + buyer_name if buyer_name else ''}, grazie per l'interesse!"
    profit = profit_at(offer)
    waiting = value_of_waiting(asking, cost, reference, profit_at, fit)
    if offer < floor * 0.85:
        return OfferDecision(
            "decline",
            None,
            profit,
            waiting,
            f"Sotto il minimo accettabile ({_eur(floor)}): il margine richiesto non resta.",
            f"{hello} Purtroppo a {_eur(offer)} non riesco ad accettare. "
            "Se ti va, ne riparliamo. Grazie comunque!",  # the floor is the seller's own limit: never said to the buyer
        )
    if offer >= floor and profit >= waiting:
        return OfferDecision(
            "accept",
            None,
            profit,
            waiting,
            "L'offerta vale almeno quanto aspettare: guadagno certo adesso contro un incerto dopo.",
            f"{hello} Va bene, accetto {_eur(offer)}. Spedisco appena possibile!",
        )
    counter = round(max(floor, min(asking, (offer + asking) / 2)), 2)
    if counter <= offer:
        counter = round(max(floor, offer), 2)
    return OfferDecision(
        "counter",
        counter,
        profit,
        waiting,
        "Sopra il minimo ma sotto il valore di attesa: controfferta a metà strada, mai sotto il minimo.",
        f"{hello} A {_eur(offer)} sono un po' sotto, potrei arrivare a {_eur(counter)}. Che ne dici?",
    )
