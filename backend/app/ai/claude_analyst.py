"""LLM-backed Deal Analyst (Claude) with hard guardrails.

The model receives only numbers computed by the deterministic engines and writes the narrative
(verdict, reasons, risks). Guardrails keep it honest:

* hard SKIP conditions (no market value, non-positive expected profit, very high risk,
  counterfeit wording) override any LLM verdict;
* the suggested maximum offer can never exceed the computed maximum buy price;
* the recommended resale price is clamped to the [quick, optimistic] range.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from app.ai.deal_analyst import (
    DealAnalysis,
    DealAnalyst,
    DealContext,
    RuleBasedDealAnalyst,
    guardrail_verdict,
)
from app.ai.llm import LLMClient
from app.domain.enums import Verdict

SYSTEM_PROMPT = """Sei un analista esperto di reselling di abbigliamento usato su Vinted.
Ricevi i dati di un annuncio e le metriche calcolate da un motore statistico (valore di mercato,
scenari di rivendita, costi, profitto, ROI, domanda, velocità, rischio). Il tuo compito è dare un
verdetto d'acquisto motivato per un reseller.

Regole:
- Usa SOLO i numeri forniti; non inventare prezzi, percentuali o dati di mercato.
- Il verdetto è BUY (comprare), CONSIDER (valutare/trattare) o SKIP (lasciar perdere).
- Non affermare mai che un prodotto è autentico: al massimo che non emergono segnali d'allarme.
- Considera che i nuovi venditori non sono automaticamente truffatori.
- Scrivi in italiano, in modo concreto e sintetico (2-4 frasi per il riepilogo, punti brevi).
- recommended_resale_price deve stare tra il prezzo di vendita rapida e quello ottimistico.
- suggested_max_offer non deve superare il prezzo massimo d'acquisto fornito."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["BUY", "CONSIDER", "SKIP"]},
        "summary": {"type": "string"},
        "pros": {"type": "array", "items": {"type": "string"}},
        "cons": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "recommended_resale_price": {"type": ["number", "null"]},
        "suggested_max_offer": {"type": ["number", "null"]},
    },
    "required": [
        "verdict",
        "summary",
        "pros",
        "cons",
        "risks",
        "recommended_resale_price",
        "suggested_max_offer",
    ],
    "additionalProperties": False,
}


def _clamp(value: Decimal | None, lo: Decimal | None, hi: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    if lo is not None and value < lo:
        value = lo
    if hi is not None and value > hi:
        value = hi
    return value.quantize(Decimal("1"))


class ClaudeDealAnalyst(DealAnalyst):
    name = "claude"

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm
        self.fallback = RuleBasedDealAnalyst()

    async def analyze(self, ctx: DealContext) -> DealAnalysis:
        payload = ctx.model_dump(mode="json")
        data = await self.llm.structured(
            system=SYSTEM_PROMPT,
            content=[
                {
                    "type": "text",
                    "text": "Dati dell'annuncio e metriche:\n" + json.dumps(payload, ensure_ascii=False),
                }
            ],
            schema=SCHEMA,
            purpose="deal_analysis",
        )
        if data is None:
            return self.fallback.analyze_sync(ctx)
        try:
            verdict = Verdict(data["verdict"])
        except (KeyError, ValueError):
            return self.fallback.analyze_sync(ctx)
        forced = guardrail_verdict(ctx)
        if forced is not None:
            verdict = forced
        quick = ctx.scenario("conservative")
        optimistic = ctx.scenario("optimistic")
        resale = data.get("recommended_resale_price")
        max_offer = data.get("suggested_max_offer")
        max_offer_dec = Decimal(str(max_offer)) if max_offer is not None else ctx.max_buy_price
        if ctx.max_buy_price is not None and max_offer_dec is not None:
            max_offer_dec = min(max_offer_dec, ctx.max_buy_price)
        return DealAnalysis(
            verdict=verdict,
            summary=str(data.get("summary", ""))[:1500],
            pros=[str(p)[:200] for p in data.get("pros", [])][:6],
            cons=[str(c)[:200] for c in data.get("cons", [])][:6],
            risks=[str(r)[:200] for r in data.get("risks", [])][:6],
            recommended_resale_price=_clamp(
                Decimal(str(resale)) if resale is not None else None,
                quick.sale_price if quick else None,
                optimistic.sale_price if optimistic else None,
            ),
            suggested_max_offer=max_offer_dec.quantize(Decimal("0.01"))
            if max_offer_dec is not None
            else None,
            provider=self.name,
            model=self.llm.model,
        )
