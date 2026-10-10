"""What a model review does to a stored opportunity: it may lower the verdict, never raise it.

One place for the rule, used when a review is written (``app.ai.service``) and when a re-analysis keeps a review
that is still valid (``app.opportunities.pipeline``). The decision engine owns the verdict and every number; the
review adds a narrative and, when it finds a reason for more caution, a lower verdict with the reason on record.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.core.config import Settings
from app.decision.engine import DecisionVerdict, apply_review_ceiling
from app.domain.enums import Verdict

# ``ai_provider`` values written by a model review; anything else ("rules") is the deterministic analyst.
LLM_PROVIDERS = frozenset({"claude", "gemini"})
REVIEW_LABEL = "La revisione dell'analista AI consiglia più cautela"

_CEILING = {Verdict.CONSIDER: DecisionVerdict.WATCHLIST, Verdict.SKIP: DecisionVerdict.PASS}
_RANK = {Verdict.SKIP: 0, Verdict.CONSIDER: 1, Verdict.BUY: 2}


def provider_label(settings: Settings) -> str:
    """The name stored with a model review: the vendor that wrote it (older rows say "claude")."""
    return "gemini" if settings.ai_provider == "gemini" else "claude"


def is_llm_provider(name: str | None) -> bool:
    return name in LLM_PROVIDERS


def has_valid_review(opp: Any) -> bool:
    """The opportunity carries a model review written for its current analysis."""
    return (
        is_llm_provider(opp.ai_provider)
        and opp.ai_for_analysis_id is not None
        and opp.ai_for_analysis_id == opp.analysis_id
    )


def reviewed_fields(
    decision: dict[str, Any] | None, verdict: str, analysis_verdict: Verdict
) -> dict[str, Any]:
    """Column values after a review of ``analysis_verdict``: the legacy verdict is the lower of the two, and when
    the review is more cautious than the decision, the decision is lowered (reason recorded as a binding veto).
    ``apply_review_ceiling`` is a no-op unless it lowers, so nothing here can raise a verdict."""
    current = Verdict(verdict)
    out: dict[str, Any] = {
        "verdict": (analysis_verdict if _RANK[analysis_verdict] < _RANK[current] else current).value
    }
    ceiling = _CEILING.get(analysis_verdict)
    if decision and ceiling is not None:
        lowered = apply_review_ceiling(decision, ceiling, REVIEW_LABEL)
        if lowered is not decision:
            out |= {
                "decision": lowered,
                "decision_verdict": lowered["verdict"],
                "recommended_action": lowered["action"],
            }
    return out


def bounded_offer(value: Decimal | None, max_buy_price: Decimal | None) -> Decimal | None:
    """The offer a model suggested, held to the computed maximum buy price (and not below zero). Where the engine
    names no maximum (no price meets the targets) there is no offer: the model's number is not shown in its place."""
    if value is None or max_buy_price is None:
        return None
    return max(Decimal(0), min(value, max_buy_price)).quantize(Decimal("0.01"))


def bounded_resale(
    value: Decimal | None, quick_sale_price: Decimal | None, optimistic_sale_price: Decimal | None
) -> Decimal | None:
    """The resale price a model suggested, held to the computed quick-optimistic range. Without both ends of the
    range there is nothing to hold it to, so there is no price."""
    if value is None or quick_sale_price is None or optimistic_sale_price is None:
        return None
    return min(max(value, quick_sale_price), optimistic_sale_price).quantize(Decimal("1"))


def reclamp_analysis(
    stored: dict[str, Any],
    *,
    max_buy_price: Decimal | None,
    quick_sale_price: Decimal | None,
    optimistic_sale_price: Decimal | None,
) -> dict[str, Any]:
    """A stored review carried over to a re-computed analysis: its two numbers are held to the new computed ones
    (the offer under the maximum buy price, the resale price inside the quick-optimistic range; the same rule as a
    fresh review, ``bounded_offer`` and ``bounded_resale``), so the text is kept and the figures stay the engine's."""
    out = dict(stored)
    offer = out.get("suggested_max_offer")
    if offer is not None:
        kept_offer = bounded_offer(Decimal(str(offer)), max_buy_price)
        out["suggested_max_offer"] = float(kept_offer) if kept_offer is not None else None
    resale = out.get("recommended_resale_price")
    if resale is not None:
        kept_resale = bounded_resale(Decimal(str(resale)), quick_sale_price, optimistic_sale_price)
        out["recommended_resale_price"] = float(kept_resale) if kept_resale is not None else None
    return out
