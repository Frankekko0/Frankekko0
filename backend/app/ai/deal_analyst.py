"""AI Deal Analyst: structured BUY / CONSIDER / SKIP verdict with reasons.

Two implementations share the same contract:

* :class:`RuleBasedDealAnalyst` - deterministic, always available, runs in the pipeline;
* ``ClaudeDealAnalyst`` (``app.ai.claude_analyst``) - LLM narrative on top of the same numbers,
  used when an API key is configured. The LLM never invents numbers: it receives the computed
  metrics and its verdict is checked against hard guardrails.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from pydantic import BaseModel, Field

from app.domain.enums import Verdict
from app.schemas.common import Money


class ScenarioSummary(BaseModel):
    name: str
    sale_price: Money
    net_profit: Money
    roi: Decimal


class DealContext(BaseModel):
    """Everything the analyst may use. All numbers come from the deterministic engines."""

    title: str
    brand: str | None = None
    category: str | None = None
    model: str | None = None
    condition: str
    size: str | None = None
    listing_price: Money
    total_acquisition_cost: Money
    fair_market_value: Money | None = None
    discount_vs_market: float | None = None
    scenarios: list[ScenarioSummary] = Field(default_factory=list)
    max_buy_price: Money | None = None
    good_buy_price: Money | None = None
    suggested_offer: Money | None = None
    demand_level: str
    sell_through_rate: float
    estimated_days_to_sell: float
    flip_score: int
    confidence_score: int
    risk_score: int
    risk_factors: list[str] = Field(default_factory=list)
    seller_summary: str | None = None
    identification_confidence: int = 0
    comparables_used: int = 0
    sold_comparables: int = 0
    market_notes: list[str] = Field(default_factory=list)
    suspicious_terms: list[str] = Field(default_factory=list)
    defect_terms: list[str] = Field(default_factory=list)
    is_vintage: bool = False
    # The decision engine's verdict (see ``app.decision``): an analyst may agree with it or be more
    # cautious, never more permissive.
    decision_verdict: str | None = None

    def scenario(self, name: str) -> ScenarioSummary | None:
        return next((s for s in self.scenarios if s.name == name), None)


class DealAnalysis(BaseModel):
    verdict: Verdict
    summary: str
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    recommended_resale_price: Money | None = None
    suggested_max_offer: Money | None = None
    provider: str = "rules"
    model: str | None = None


class DealAnalyst(ABC):
    name: str = "abstract"

    @abstractmethod
    async def analyze(self, ctx: DealContext) -> DealAnalysis: ...


def guardrail_verdict(ctx: DealContext) -> Verdict | None:
    """Hard constraints no analyst (human-written rules or LLM) may override."""
    expected = ctx.scenario("expected")
    if ctx.fair_market_value is None or expected is None:
        return Verdict.SKIP
    if expected.net_profit <= 0 or ctx.risk_score >= 75 or ctx.suspicious_terms:
        return Verdict.SKIP
    return None


_LEGACY_RANK = {Verdict.SKIP: 0, Verdict.CONSIDER: 1, Verdict.BUY: 2}


def decision_ceiling(ctx: DealContext) -> Verdict | None:
    """The verdict the decision engine allows, in the three-valued vocabulary of the analysts."""
    if ctx.decision_verdict is None:
        return None
    from app.decision.engine import DecisionVerdict

    return DecisionVerdict(ctx.decision_verdict).legacy


def clamp_to_decision(ctx: DealContext, verdict: Verdict) -> Verdict:
    """Lower ``verdict`` to what the decision engine allows; never raise it."""
    ceiling = decision_ceiling(ctx)
    if ceiling is not None and _LEGACY_RANK[verdict] > _LEGACY_RANK[ceiling]:
        return ceiling
    return verdict


def rule_verdict(ctx: DealContext) -> Verdict:
    ceiling = decision_ceiling(ctx)
    if ceiling is not None:
        return ceiling  # the decision engine is the one place a verdict is made
    forced = guardrail_verdict(ctx)
    if forced is not None:
        return forced
    conservative = ctx.scenario("conservative")
    if (
        ctx.flip_score >= 75
        and ctx.confidence_score >= 55
        and ctx.risk_score < 50
        and conservative is not None
        and conservative.net_profit > 0
    ):
        return Verdict.BUY
    if ctx.flip_score < 45:
        return Verdict.SKIP
    return Verdict.CONSIDER


def _eur(v: Decimal | None) -> str:
    if v is None:
        return "n/d"
    return f"{v:.0f} €" if v == v.to_integral_value() else f"{v:.2f} €"


def _pct(v: Decimal | float) -> str:
    return f"{float(v) * 100:.0f}%"


DEMAND_IT = {
    "very_high": "molto alta",
    "high": "elevata",
    "medium": "nella media",
    "low": "bassa",
    "very_low": "molto bassa",
}


class RuleBasedDealAnalyst(DealAnalyst):
    name = "rules"

    async def analyze(self, ctx: DealContext) -> DealAnalysis:
        return self.analyze_sync(ctx)

    def analyze_sync(self, ctx: DealContext) -> DealAnalysis:
        verdict = rule_verdict(ctx)
        expected = ctx.scenario("expected")
        conservative = ctx.scenario("conservative")
        optimistic = ctx.scenario("optimistic")
        pros: list[str] = []
        cons: list[str] = []
        risks: list[str] = list(ctx.risk_factors[:5])

        sentences: list[str] = []
        if ctx.fair_market_value is None:
            summary = (
                "Non ci sono abbastanza dati per stimare con affidabilità il prezzo di mercato: "
                "senza un valore di riferimento non è possibile valutare il margine."
            )
            return DealAnalysis(
                verdict=Verdict.SKIP,
                summary=summary,
                cons=["Valore di mercato non stimabile"],
                risks=risks,
                provider=self.name,
            )

        d = ctx.discount_vs_market or 0.0
        if d >= 0.1:
            sentences.append(
                f"Il prodotto è quotato circa il {d * 100:.0f}% sotto la mediana degli articoli comparabili."
            )
            pros.append(f"Prezzo {d * 100:.0f}% sotto il valore di mercato ({_eur(ctx.fair_market_value)})")
        elif d > -0.05:
            sentences.append("Il prezzo è in linea con il valore di mercato stimato.")
            cons.append("Nessuno sconto significativo rispetto al mercato")
        else:
            sentences.append(f"Il prezzo è circa il {abs(d) * 100:.0f}% sopra il valore di mercato stimato.")
            cons.append("Prezzo superiore al valore di mercato")

        demand_it = DEMAND_IT.get(ctx.demand_level, ctx.demand_level)
        if ctx.demand_level in ("high", "very_high"):
            pros.append(f"Domanda {demand_it} (sell-through {ctx.sell_through_rate * 100:.0f}%)")
        elif ctx.demand_level in ("low", "very_low"):
            cons.append(f"Domanda {demand_it}: rivendita potenzialmente lenta")
        if ctx.estimated_days_to_sell <= 7:
            pros.append(f"Rivendita stimata in circa {ctx.estimated_days_to_sell:.0f} giorni")
        elif ctx.estimated_days_to_sell > 21:
            cons.append(f"Tempo di rivendita stimato lungo (~{ctx.estimated_days_to_sell:.0f} giorni)")

        if conservative and conservative.net_profit > 0 and conservative.roi >= Decimal("0.3"):
            sentences.append(
                f"La domanda per questo modello è {demand_it} e la stima di rivendita conservativa "
                f"({_eur(conservative.sale_price)}) offre comunque un ROI del {_pct(conservative.roi)}."
            )
            pros.append(f"Profittevole anche vendendo velocemente ({_eur(conservative.net_profit)} netti)")
        elif expected:
            sentences.append(
                f"Allo scenario realistico ({_eur(expected.sale_price)}) il profitto netto è {_eur(expected.net_profit)} "
                f"con ROI {_pct(expected.roi)}."
            )
            if conservative and conservative.net_profit <= 0:
                cons.append("Con una vendita rapida a prezzo basso il margine si azzera")

        if ctx.flip_score >= 80:
            pros.append(f"Flip Score {ctx.flip_score}/100")
        if ctx.confidence_score < 50:
            cons.append(f"Affidabilità dell'analisi limitata ({ctx.confidence_score}/100)")
        if ctx.comparables_used < 8:
            cons.append(f"Solo {ctx.comparables_used} comparabili utilizzati")
        if ctx.defect_terms:
            cons.append("Difetti dichiarati: " + ", ".join(ctx.defect_terms))
        if ctx.suspicious_terms:
            cons.append("Termini sospetti nella descrizione: possibile non originale")
        if ctx.is_vintage:
            pros.append("Articolo vintage: mercato dedicato e collezionisti")

        if verdict == Verdict.BUY:
            sentences.append(
                f"Consigliato l'acquisto: non superare {_eur(ctx.max_buy_price)} per rispettare i tuoi obiettivi."
            )
        elif verdict == Verdict.CONSIDER:
            sentences.append("Valuta con attenzione: verifica foto e dettagli o prova un'offerta.")
        else:
            sentences.append("Sconsigliato con i dati attuali.")

        resale = expected.sale_price if expected else None
        if (
            optimistic
            and ctx.demand_level in ("high", "very_high")
            and ctx.estimated_days_to_sell <= 7
            and expected is not None
        ):
            resale = ((expected.sale_price + optimistic.sale_price) / 2).quantize(Decimal("1"))
        return DealAnalysis(
            verdict=verdict,
            summary=" ".join(sentences),
            pros=pros[:6],
            cons=cons[:6],
            risks=risks,
            recommended_resale_price=resale,
            suggested_max_offer=ctx.max_buy_price,
            provider=self.name,
        )
