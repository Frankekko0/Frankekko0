"""The decision: one verdict from the evidence, with the reasons, the warnings and what is missing.

Pure (no I/O), deterministic, and the only place where a verdict is made. The verdicts are

    STRONG BUY · BUY · NEGOTIATE · WATCHLIST · PASS · INSUFFICIENT EVIDENCE

and four scores stay separate: Flip (how attractive), Confidence (how sure the analysis is),
Risk (how likely it goes wrong) and Data completeness (how much of the listing could be read).
Nothing is decided on the Flip Score alone: a 95 with confidence 30 is "very uncertain".

How it decides:

1. *Insufficient evidence* is a state of its own, checked first: no reliable market value, or too
   little of the listing could be read. A high ROI cannot buy it back.
2. The economics propose a *candidate* verdict (STRONG BUY ... PASS).
3. *Vetoes* put a ceiling on it. They are not compensable: counterfeit risk, a risky brand without
   proof of authenticity, an unavailable listing, an analysis too uncertain to act on.
4. STRONG BUY is only reached when every requirement is met and shown (model, total cost,
   comparables, condition, photos, authenticity), so "what is still missing for a STRONG BUY" is
   always a readable list.

Thresholds are data (:class:`DecisionConfig`), not scattered constants, and are versioned.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from app.decision.completeness import MissingInfo
from app.domain.enums import RecommendedAction, Verdict

RULES_VERSION = "decision-v1"


class DecisionVerdict(StrEnum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    NEGOTIATE = "NEGOTIATE"
    WATCHLIST = "WATCHLIST"
    PASS = "PASS"  # noqa: S105 - a verdict, not a password
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ")

    @property
    def rank(self) -> int:
        return _RANK[self]

    @property
    def legacy(self) -> Verdict:
        """The three-valued verdict older consumers (feed filters, exports) still read."""
        if self in (DecisionVerdict.STRONG_BUY, DecisionVerdict.BUY):
            return Verdict.BUY
        if self in (DecisionVerdict.NEGOTIATE, DecisionVerdict.WATCHLIST):
            return Verdict.CONSIDER
        return Verdict.SKIP


_RANK = {
    DecisionVerdict.INSUFFICIENT_EVIDENCE: -1,
    DecisionVerdict.PASS: 0,
    DecisionVerdict.WATCHLIST: 1,
    DecisionVerdict.NEGOTIATE: 2,
    DecisionVerdict.BUY: 3,
    DecisionVerdict.STRONG_BUY: 4,
}


@dataclass(frozen=True)
class DecisionConfig:
    # STRONG BUY: every one of these, plus the requirements listed in ``strong_buy_requirements``.
    strong_flip: int = 80
    strong_confidence: int = 70
    strong_completeness: int = 70
    strong_risk_below: int = 35
    strong_min_photos: int = 3
    strong_min_comparables: int = 8
    strong_min_market_confidence: int = 60
    strong_min_identification: int = 70
    # Downside (Monte Carlo): the chance of losing money. It cannot be small: an item that is counterfeit
    # (up to ~15% even when "probably authentic") or comes back already costs the purchase.
    strong_max_p_loss: float = 0.30
    # BUY
    buy_flip: int = 65
    buy_confidence: int = 50
    buy_completeness: int = 45
    buy_risk_below: int = 55
    # NEGOTIATE: worth it at a lower price that a seller may plausibly accept.
    negotiate_confidence: int = 45
    negotiate_risk_below: int = 60
    min_offer_ratio: Decimal = Decimal("0.75")
    # WATCHLIST: promising enough to keep an eye on.
    watch_flip: int = 45
    watch_floor_ratio: Decimal = Decimal("0.65")
    # Vetoes and states.
    min_completeness: int = 30
    min_confidence_to_act: int = 40
    brand_at_risk: float = 0.3
    high_risk: int = 75
    high_seller_risk: int = 70  # a scam-risk seller: no purchase whatever the margin


DEFAULT_CONFIG = DecisionConfig()


@dataclass(frozen=True)
class DecisionInput:
    # Money (EUR). ``expected``/``conservative`` are the net profit of each scenario.
    price: Decimal
    expected_profit: Decimal | None
    conservative_profit: Decimal | None
    expected_roi: Decimal | None
    max_buy_price: Decimal | None
    suggested_offer: Decimal | None
    min_profit: Decimal
    min_roi: Decimal
    # The four scores.
    flip_score: int
    confidence_score: int
    risk_score: int
    completeness_score: int
    # Evidence.
    data_quality: str  # ok | limited | insufficient
    comparables_used: int
    market_confidence: int
    identification_confidence: int
    model_known: bool
    condition_known: bool
    photo_count: int
    acquisition_cost_verified: bool
    # A photo analysis really ran (condition and defects checked), and a label photo was seen.
    photos_analysed: bool = False
    label_photo_seen: bool | None = None
    brand_counterfeit_risk: float = 0.1
    authenticity_verdict: str | None = (
        None  # probably_authentic | uncertain | not_verifiable | counterfeit_risk
    )
    suspicious_terms: bool = False
    status: str = "active"
    discount_vs_market: float | None = None
    demand_level: str = "medium"
    insufficient_reason: str | None = None
    condition_downgraded: bool = False
    unknown_costs: tuple[str, ...] = ()
    risk_labels: tuple[str, ...] = ()
    missing_info: tuple[MissingInfo, ...] = ()
    risk_adjusted_profit: float | None = None
    offer_action: RecommendedAction | None = None
    # Monte Carlo (None: not simulated, which a Strong buy does not accept): chance of a loss, mean profit
    # after the risks, and the unfavourable scenario (P10, shown, not a requirement). Seller scam risk.
    p_loss: float | None = None
    profit_mean: float | None = None
    profit_p10: float | None = None
    seller_risk_score: int = 0


@dataclass(frozen=True)
class Veto:
    code: str
    label: str
    ceiling: DecisionVerdict
    binding: bool = False  # True when it actually lowered the verdict

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "ceiling": self.ceiling.value,
            "binding": self.binding,
        }


@dataclass(frozen=True)
class Requirement:
    code: str
    label: str
    met: bool

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "label": self.label, "met": self.met}


@dataclass
class Decision:
    verdict: DecisionVerdict
    scores: dict[str, int]
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_info: list[dict[str, str]] = field(default_factory=list)
    vetoes: list[Veto] = field(default_factory=list)
    strong_buy_requirements: list[Requirement] = field(default_factory=list)
    threshold_price: Decimal | None = None  # the price at which it becomes (or stays) a deal
    rank_value: float = 0.0
    action: RecommendedAction = RecommendedAction.WATCH
    candidate: DecisionVerdict = DecisionVerdict.PASS
    rules_version: str = RULES_VERSION
    # Distribution of the profit, pre-mortem, value of information, seller risk (attached by the analysis).
    intelligence: dict[str, Any] | None = None

    @property
    def legacy_verdict(self) -> Verdict:
        return self.verdict.legacy

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "label": self.verdict.label,
            "legacy_verdict": self.legacy_verdict.value,
            "action": self.action.value,
            "scores": dict(self.scores),
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "missing_info": list(self.missing_info),
            "vetoes": [v.as_dict() for v in self.vetoes],
            "strong_buy_requirements": [r.as_dict() for r in self.strong_buy_requirements],
            "threshold_price": str(self.threshold_price) if self.threshold_price is not None else None,
            "rank_value": round(self.rank_value, 2),
            "candidate": self.candidate.value,
            "rules_version": self.rules_version,
            "intelligence": self.intelligence,
        }


def _eur(v: Decimal) -> str:
    return f"€{v:.0f}" if v == v.to_integral_value() else f"€{v:.2f}"


def rank_value(risk_adjusted_profit: float | None, confidence: int) -> float:
    """What to sort by: the risk-adjusted profit, discounted when the analysis is unsure.

    An uncertain 20 EUR on a slow item is worth less than a sure 15 EUR on one that sells (case H):
    the sale probability is already inside the risk-adjusted profit, confidence scales the rest.
    """
    if risk_adjusted_profit is None:
        return 0.0
    return risk_adjusted_profit * (0.4 + 0.6 * max(0, min(100, confidence)) / 100)


def strong_buy_requirements(inp: DecisionInput, cfg: DecisionConfig = DEFAULT_CONFIG) -> list[Requirement]:
    at_or_below_max = inp.max_buy_price is not None and inp.price <= inp.max_buy_price
    meets = _meets_targets(inp)
    brand_ok = (
        inp.brand_counterfeit_risk < cfg.brand_at_risk or inp.authenticity_verdict == "probably_authentic"
    )
    return [
        Requirement("flip", f"Flip Score almeno {cfg.strong_flip}", inp.flip_score >= cfg.strong_flip),
        Requirement(
            "confidence",
            f"Confidenza almeno {cfg.strong_confidence}",
            inp.confidence_score >= cfg.strong_confidence,
        ),
        Requirement(
            "completeness",
            f"Dati dell'annuncio completi almeno al {cfg.strong_completeness}%",
            inp.completeness_score >= cfg.strong_completeness,
        ),
        Requirement("risk", f"Rischio sotto {cfg.strong_risk_below}", inp.risk_score < cfg.strong_risk_below),
        Requirement(
            "targets", "Prezzo entro la soglia d'acquisto e obiettivi rispettati", meets and at_or_below_max
        ),
        Requirement(
            "conservative_profit",
            "Profitto positivo anche nello scenario prudente",
            inp.conservative_profit is not None and inp.conservative_profit > 0,
        ),
        Requirement(
            "comparables",
            f"Comparabili verificati (almeno {cfg.strong_min_comparables}, confidenza di mercato "
            f"{cfg.strong_min_market_confidence})",
            inp.data_quality == "ok"
            and inp.comparables_used >= cfg.strong_min_comparables
            and inp.market_confidence >= cfg.strong_min_market_confidence,
        ),
        Requirement(
            "model",
            "Modello identificato",
            inp.model_known and inp.identification_confidence >= cfg.strong_min_identification,
        ),
        Requirement("condition", "Condizione dichiarata", inp.condition_known),
        Requirement(
            "photos_checked", "Foto analizzate: condizioni e difetti verificati", inp.photos_analysed
        ),
        Requirement("label", "Etichetta visibile nelle foto", inp.label_photo_seen is True),
        Requirement("total_cost", "Costo totale letto dall'annuncio", inp.acquisition_cost_verified),
        Requirement(
            "photos", f"Almeno {cfg.strong_min_photos} foto", inp.photo_count >= cfg.strong_min_photos
        ),
        Requirement("authenticity", "Autenticità non a rischio per questa marca", brand_ok),
        Requirement(
            "downside",
            f"Probabilità di perdita al massimo {cfg.strong_max_p_loss:.0%} e profitto medio positivo dopo i rischi",
            inp.p_loss is not None
            and inp.p_loss <= cfg.strong_max_p_loss
            and inp.profit_mean is not None
            and inp.profit_mean > 0,
        ),
        Requirement("available", "Annuncio disponibile", inp.status == "active"),
    ]


def _meets_targets(inp: DecisionInput) -> bool:
    return (
        inp.expected_profit is not None
        and inp.expected_roi is not None
        and inp.expected_profit >= inp.min_profit
        and inp.expected_roi >= inp.min_roi
    )


def decide(inp: DecisionInput, cfg: DecisionConfig = DEFAULT_CONFIG) -> Decision:
    scores = {
        "flip": inp.flip_score,
        "confidence": inp.confidence_score,
        "risk": inp.risk_score,
        "completeness": inp.completeness_score,
    }
    missing = [m.as_dict() for m in inp.missing_info]
    value = rank_value(inp.risk_adjusted_profit, inp.confidence_score)

    # ---- 1. insufficient evidence: a state, not a low score ---------------------------------
    if inp.data_quality == "insufficient" or inp.completeness_score < cfg.min_completeness:
        why = (
            inp.insufficient_reason
            if inp.data_quality == "insufficient" and inp.insufficient_reason
            else f"Dell'annuncio si legge troppo poco (completezza {inp.completeness_score}/100)."
        )
        veto = Veto("insufficient_evidence", why, DecisionVerdict.INSUFFICIENT_EVIDENCE, binding=True)
        return Decision(
            verdict=DecisionVerdict.INSUFFICIENT_EVIDENCE,
            scores=scores,
            warnings=[why],
            missing_info=missing,
            vetoes=[veto],
            strong_buy_requirements=strong_buy_requirements(inp, cfg),
            threshold_price=None,
            rank_value=0.0,
            action=RecommendedAction.WATCH,
            candidate=DecisionVerdict.INSUFFICIENT_EVIDENCE,
        )

    # ---- 2. the candidate verdict, from the economics ---------------------------------------
    requirements = strong_buy_requirements(inp, cfg)
    meets = _meets_targets(inp)
    max_buy = inp.max_buy_price
    at_or_below_max = max_buy is not None and inp.price <= max_buy
    negotiable = (
        max_buy is not None
        and inp.price > max_buy
        and max_buy >= inp.price * cfg.min_offer_ratio
        and inp.confidence_score >= cfg.negotiate_confidence
        and inp.risk_score < cfg.negotiate_risk_below
    )
    if all(r.met for r in requirements):
        candidate = DecisionVerdict.STRONG_BUY
    elif (
        meets
        and at_or_below_max
        and inp.flip_score >= cfg.buy_flip
        and inp.confidence_score >= cfg.buy_confidence
        and inp.completeness_score >= cfg.buy_completeness
        and inp.risk_score < cfg.buy_risk_below
        and inp.conservative_profit is not None
        and inp.conservative_profit >= 0
    ):
        candidate = DecisionVerdict.BUY
    elif negotiable:
        candidate = DecisionVerdict.NEGOTIATE
    elif (
        max_buy is not None
        and max_buy >= inp.price * cfg.watch_floor_ratio
        and (inp.price > max_buy or inp.flip_score >= cfg.watch_flip)
    ):
        # Worth keeping an eye on: a plausible price drop gets there (the Flip Score is capped at the
        # asking price while it loses money, so it cannot be asked), or it works but is not yet good.
        candidate = DecisionVerdict.WATCHLIST
    else:
        candidate = DecisionVerdict.PASS

    # ---- 3. vetoes: ceilings that no profit can lift ------------------------------------------
    brand_at_risk = inp.brand_counterfeit_risk >= cfg.brand_at_risk
    pending: list[tuple[str, str, DecisionVerdict, bool]] = [
        (
            "not_available",
            "L'annuncio non risulta disponibile",
            DecisionVerdict.PASS if inp.status in ("sold", "removed") else DecisionVerdict.WATCHLIST,
            inp.status != "active",
        ),
        (
            "counterfeit_risk",
            "Rischio contraffazione elevato: non compensabile da un buon margine",
            DecisionVerdict.PASS,
            inp.authenticity_verdict == "counterfeit_risk" or inp.suspicious_terms,
        ),
        (
            "seller_scam_risk",
            f"Venditore ad alto rischio truffa ({inp.seller_risk_score}/100): non si compra",
            DecisionVerdict.PASS,
            inp.seller_risk_score >= cfg.high_seller_risk,
        ),
        (
            "very_high_risk",
            f"Rischio complessivo molto alto ({inp.risk_score}/100)",
            DecisionVerdict.PASS,
            inp.risk_score >= cfg.high_risk,
        ),
        (
            "authenticity_unproven",
            "Marca spesso contraffatta senza prove sufficienti di autenticità: chiedi la foto dell'etichetta",
            DecisionVerdict.WATCHLIST,
            brand_at_risk and inp.authenticity_verdict not in ("probably_authentic", "counterfeit_risk"),
        ),
        (
            "low_confidence",
            f"Analisi troppo incerta per agire (confidenza {inp.confidence_score}/100)",
            DecisionVerdict.WATCHLIST,
            inp.confidence_score < cfg.min_confidence_to_act,
        ),
    ]
    verdict = candidate
    vetoes: list[Veto] = []
    for code, label, ceiling, active in pending:
        if not active:
            continue
        binding = candidate.rank > ceiling.rank
        vetoes.append(Veto(code, label, ceiling, binding))
        if verdict.rank > ceiling.rank:
            verdict = ceiling

    # ---- 4. what to say ---------------------------------------------------------------------
    reasons: list[str] = []
    warnings: list[str] = [v.label for v in vetoes if v.binding]
    if inp.discount_vs_market is not None:
        pct = round(inp.discount_vs_market * 100)
        if pct >= 10:
            reasons.append(f"Prezzo {pct}% sotto il mercato")
        elif pct <= -5:
            warnings.append(f"Prezzo {-pct}% sopra il mercato")
    if inp.expected_profit is not None and inp.expected_roi is not None:
        roi_pct = round(float(inp.expected_roi) * 100)
        line = f"Profitto atteso {_eur(inp.expected_profit)} (ROI {roi_pct}%)"
        (reasons if inp.expected_profit > 0 else warnings).append(line)
    if inp.conservative_profit is not None and inp.conservative_profit > 0:
        reasons.append(f"Anche nello scenario prudente resta in profitto ({_eur(inp.conservative_profit)})")
    elif inp.conservative_profit is not None:
        warnings.append("Nello scenario prudente non c'è profitto")
    if inp.demand_level in ("high", "very_high"):
        reasons.append("Domanda alta")
    elif inp.demand_level in ("low", "very_low"):
        warnings.append("Domanda bassa")
    if at_or_below_max and max_buy is not None:
        reasons.append(f"Prezzo entro la soglia d'acquisto di {_eur(max_buy)}")
    if inp.condition_downgraded:
        warnings.append(
            "Dalle foto le condizioni risultano peggiori di quelle dichiarate: prezzi stimati sulle foto"
        )
    if inp.data_quality == "limited":
        warnings.append(f"Stima indicativa: basata su {inp.comparables_used} comparabili")
    if inp.unknown_costs:
        warnings.append("Profitto al lordo di: " + ", ".join(inp.unknown_costs))
    for label in inp.risk_labels[:3]:
        if label not in warnings:
            warnings.append(label)

    threshold: Decimal | None = None
    if verdict in (DecisionVerdict.NEGOTIATE, DecisionVerdict.WATCHLIST) and max_buy is not None:
        threshold = max_buy
        if verdict == DecisionVerdict.NEGOTIATE:
            offer = inp.suggested_offer if inp.suggested_offer is not None else max_buy
            reasons.append(f"Conviene a {_eur(max_buy)} o meno: apri con {_eur(min(offer, max_buy))}")
        elif inp.price > max_buy:
            reasons.append(f"Diventa interessante sotto {_eur(max_buy)}")
    elif verdict in (DecisionVerdict.STRONG_BUY, DecisionVerdict.BUY):
        threshold = max_buy
    if verdict == DecisionVerdict.STRONG_BUY:
        reasons.insert(
            0, "Tutti i requisiti verificati: modello, costo totale, comparabili, condizioni, foto"
        )
    elif candidate == DecisionVerdict.STRONG_BUY or verdict == DecisionVerdict.BUY:
        for r in requirements:
            if not r.met:
                missing.append({"code": f"strong_buy:{r.code}", "label": r.label, "blocks": "strong_buy"})

    return Decision(
        verdict=verdict,
        scores=scores,
        reasons=reasons,
        warnings=warnings,
        missing_info=_dedupe(missing),
        vetoes=vetoes,
        strong_buy_requirements=requirements,
        threshold_price=threshold,
        rank_value=value,
        action=action_for(verdict, inp.offer_action),
        candidate=candidate,
    )


def _dedupe(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for m in items:
        if m["code"] not in seen:
            seen.add(m["code"])
            out.append(m)
    return out


def action_for(verdict: DecisionVerdict, offered: RecommendedAction | None) -> RecommendedAction:
    """The action that matches the verdict; the offer plan's wording is kept when it agrees."""
    if verdict in (DecisionVerdict.STRONG_BUY, DecisionVerdict.BUY):
        if offered in (RecommendedAction.BUY_NOW, RecommendedAction.MAKE_OFFER):
            return offered
        return RecommendedAction.MAKE_OFFER
    if verdict == DecisionVerdict.NEGOTIATE:
        return RecommendedAction.MAKE_OFFER
    if verdict in (DecisionVerdict.WATCHLIST, DecisionVerdict.INSUFFICIENT_EVIDENCE):
        return RecommendedAction.WATCH
    return RecommendedAction.SKIP


def config_dict(cfg: DecisionConfig = DEFAULT_CONFIG) -> dict[str, Any]:
    return {k: str(v) if isinstance(v, Decimal) else v for k, v in asdict(cfg).items()}


def apply_review_ceiling(decision: dict[str, Any], ceiling: DecisionVerdict, label: str) -> dict[str, Any]:
    """Lower a stored decision to ``ceiling`` (an analyst's review found a reason); never raise it.

    The verdict, its three-valued form and the action follow, and the reason is added as a binding
    veto and as the first warning, so the lowering is visible and is not undone by reading the
    scores again.
    """
    current = DecisionVerdict(decision["verdict"])
    if current.rank <= ceiling.rank:
        return decision
    return {
        **decision,
        "verdict": ceiling.value,
        "label": ceiling.label,
        "legacy_verdict": ceiling.legacy.value,
        "action": action_for(ceiling, None).value,
        "vetoes": [
            *decision.get("vetoes", []),
            {"code": "analyst_review", "label": label, "ceiling": ceiling.value, "binding": True},
        ],
        "warnings": [label, *decision.get("warnings", [])],
        "reviewed_from": current.value,
    }
