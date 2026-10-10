"""The policy: every proposed action is checked against the limits and the switches, all violations reported.

Order of authority: the kill switch and a suspension stop everything; a disabled autonomy does nothing;
otherwise each limit is checked and *every* violation is listed (not only the first), so the audit trail
shows the whole picture. An action inside the limits during the dry-run period is *decided and recorded but
not executed*; after it, the channel decides how it reaches the user.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from app.autonomy.limits import Limits
from app.intelligence.exposure import Holding, check_exposure
from app.negotiation.assistant import pressure_phrases


class Kind(StrEnum):
    BUY = "buy"
    OFFER = "offer"
    MESSAGE = "message"
    LIST = "list"
    REPRICE = "reprice"


ACTIONABLE = {"STRONG_BUY", "BUY"}


@dataclass(frozen=True)
class OppFacts:
    verdict: str
    flip: int
    confidence: int
    risk: int
    brand: str | None = None
    category: str | None = None
    size: str | None = None
    seller: str | None = None
    price_band: str | None = None
    premortem_done: bool = False
    verifier_agrees: bool | None = None  # None: not verified (a purchase needs True)
    available: bool = True


@dataclass(frozen=True)
class Proposed:
    kind: Kind
    cost: Decimal = Decimal(0)
    opportunity: OppFacts | None = None
    text: str | None = None
    price: Decimal | None = None
    floor: Decimal | None = None
    current: Decimal | None = None  # a markdown: the asking price today (the new price must be below it)


@dataclass(frozen=True)
class Usage:
    spent_today: Decimal = Decimal(0)
    spent_week: Decimal = Decimal(0)
    owned_items: int = 0
    messages_today: int = 0
    holdings: tuple[Holding, ...] = ()
    reprices_today: int = 0


@dataclass(frozen=True)
class Switches:
    enabled: bool
    killed: bool
    suspended: bool
    dry_run_until: datetime | None
    now: datetime

    @property
    def dry_run(self) -> bool:
        return self.dry_run_until is not None and self.now < self.dry_run_until


@dataclass(frozen=True)
class Violation:
    code: str
    label: str


@dataclass
class PolicyResult:
    mode: str  # blocked | dry_run | execute
    violations: list[Violation] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.mode != "blocked"

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "violations": [{"code": v.code, "label": v.label} for v in self.violations],
        }


def evaluate(p: Proposed, limits: Limits, usage: Usage, sw: Switches) -> PolicyResult:
    if sw.killed:
        return PolicyResult(
            "blocked", [Violation("kill_switch", "Interruttore d'emergenza attivo: nessuna azione")]
        )
    if sw.suspended:
        return PolicyResult(
            "blocked", [Violation("suspended", "Autonomia sospesa per un'anomalia: serve la tua ripresa")]
        )
    if not sw.enabled:
        return PolicyResult("blocked", [Violation("disabled", "Autonomia non attiva")])

    v: list[Violation] = []
    o = p.opportunity
    if p.kind in (Kind.BUY, Kind.OFFER):
        if o is None:
            v.append(Violation("no_opportunity", "Azione senza un'opportunità analizzata"))
        else:
            allowed_verdicts = ACTIONABLE | ({"NEGOTIATE"} if p.kind == Kind.OFFER else set())
            if o.verdict not in allowed_verdicts:
                v.append(Violation("verdict", f"Il verdetto {o.verdict} non autorizza questa azione"))
            if not o.available:
                v.append(Violation("not_available", "Annuncio non verificato di recente o non disponibile"))
            if o.flip < limits.min_flip:
                v.append(Violation("min_flip", f"Flip Score {o.flip} sotto il minimo {limits.min_flip}"))
            if o.confidence < limits.min_confidence:
                v.append(
                    Violation(
                        "min_confidence", f"Confidenza {o.confidence} sotto il minimo {limits.min_confidence}"
                    )
                )
            if o.risk > limits.max_risk:
                v.append(Violation("max_risk", f"Rischio {o.risk} sopra il massimo {limits.max_risk}"))
            if limits.allowed_brands and (o.brand or "").lower() not in limits.allowed_brands:
                v.append(Violation("brand_not_allowed", f"Marca «{o.brand or 'sconosciuta'}» non consentita"))
            if limits.allowed_categories and (o.category or "").lower() not in limits.allowed_categories:
                v.append(
                    Violation(
                        "category_not_allowed", f"Categoria «{o.category or 'sconosciuta'}» non consentita"
                    )
                )
            if p.kind == Kind.BUY:
                if (
                    limits.premortem_above is not None
                    and p.cost >= limits.premortem_above
                    and not o.premortem_done
                ):
                    v.append(
                        Violation("premortem_missing", "Acquisto sopra soglia senza pre-mortem registrato")
                    )
                if o.verifier_agrees is not True:
                    v.append(
                        Violation(
                            "verifier",
                            "Il verificatore indipendente non ha confermato"
                            if o.verifier_agrees is False
                            else "Non verificato",
                        )
                    )
                held = list(usage.holdings)
                cand = Holding(float(p.cost), o.brand, o.category, o.size, o.price_band, o.seller)
                for x in check_exposure(held, cand, limits.exposure):
                    v.append(Violation("exposure", f"Concentrazione: {x.label()}"))
        if p.kind == Kind.BUY:
            if limits.daily_budget is None or limits.weekly_budget is None:
                v.append(
                    Violation("no_budget", "Nessun budget giornaliero e settimanale impostato: non si compra")
                )
            else:
                if usage.spent_today + p.cost > limits.daily_budget:
                    v.append(
                        Violation("daily_budget", f"Supera il budget giornaliero ({limits.daily_budget} €)")
                    )
                if usage.spent_week + p.cost > limits.weekly_budget:
                    v.append(
                        Violation("weekly_budget", f"Supera il budget settimanale ({limits.weekly_budget} €)")
                    )
            if limits.max_per_item is None:
                v.append(Violation("no_max_per_item", "Nessun massimo per articolo impostato"))
            elif p.cost > limits.max_per_item:
                v.append(
                    Violation(
                        "max_per_item",
                        f"Costo {p.cost} € sopra il massimo per articolo ({limits.max_per_item} €)",
                    )
                )
            if limits.max_items is not None and usage.owned_items + 1 > limits.max_items:
                v.append(
                    Violation(
                        "max_items",
                        f"Già {usage.owned_items} articoli in giacenza (massimo {limits.max_items})",
                    )
                )
    elif p.kind == Kind.MESSAGE:
        if usage.messages_today + 1 > limits.max_messages_per_day:
            v.append(
                Violation(
                    "max_messages",
                    f"Già {usage.messages_today} messaggi oggi (massimo {limits.max_messages_per_day})",
                )
            )
        if p.text is None or not p.text.strip():
            v.append(Violation("empty_message", "Messaggio vuoto"))
        elif pressure := pressure_phrases(p.text):
            v.append(
                Violation("pressure", f"Il messaggio fa pressione ({', '.join(pressure)}): non si invia")
            )
    elif p.kind == Kind.REPRICE:
        if p.price is None or p.floor is None:
            v.append(Violation("no_price", "Ribasso senza prezzo o minimo"))
        else:
            if p.price < p.floor:
                v.append(Violation("below_floor", f"Il prezzo {p.price} € è sotto il minimo {p.floor} €"))
            if p.current is not None and p.price >= p.current:
                v.append(
                    Violation(
                        "not_a_markdown",
                        f"Il prezzo {p.price} € non è inferiore a quello attuale ({p.current} €): si propongono solo ribassi",
                    )
                )
        if usage.reprices_today + 1 > limits.max_reprices_per_day:
            v.append(
                Violation(
                    "max_reprices",
                    f"Già {usage.reprices_today} ribassi proposti oggi (massimo {limits.max_reprices_per_day})",
                )
            )
    elif p.kind == Kind.LIST and (p.price is None or p.price <= 0):
        v.append(Violation("no_price", "Pubblicazione senza prezzo"))

    if v:
        return PolicyResult("blocked", v)
    return PolicyResult("dry_run" if sw.dry_run else "execute")
