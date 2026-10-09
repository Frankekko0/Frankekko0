"""Value of information: is more analysis worth what it costs?

Before paying for a deeper photo analysis or asking the seller for more pictures, ask whether the answer
could change the decision. For a risk that is either there or not (counterfeit, hidden defect), knowing
it for sure lets us buy only when the item is fine::

    VOI = (1-p) * max(profit_if_fine, 0) + p * max(profit_if_bad, 0) - max(expected_profit, 0)

It is zero when the decision does not depend on the risk (a clear PASS, or a deal good even if the risk
happens) and largest at the boundary. The extra analysis is justified only when the share of that value
the analysis can actually resolve exceeds its cost by a margin.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Voi:
    value: float  # euros
    resolvable: float  # euros the proposed analysis can capture
    cost: float
    worth_it: bool
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "voi": round(self.value, 3),
            "resolvable": round(self.resolvable, 3),
            "cost": round(self.cost, 3),
            "worth_it": self.worth_it,
            "reason": self.reason,
        }


def voi_binary(p_bad: float, profit_fine: float, profit_bad: float) -> float:
    p = max(0.0, min(1.0, p_bad))
    expected = (1 - p) * profit_fine + p * profit_bad
    return (1 - p) * max(profit_fine, 0.0) + p * max(profit_bad, 0.0) - max(expected, 0.0)


def decide_analysis(
    p_bad: float,
    profit_fine: float,
    profit_bad: float,
    cost_of_analysis: float,
    resolves: float = 0.8,
    margin: float = 1.5,
) -> Voi:
    """``resolves``: the share of the uncertainty the analysis removes (a photo check rarely settles
    authenticity completely). ``margin``: the value must exceed the cost by this factor."""
    value = voi_binary(p_bad, profit_fine, profit_bad)
    gain = value * max(0.0, min(1.0, resolves))
    if profit_fine <= 0:
        return Voi(
            value,
            gain,
            cost_of_analysis,
            False,
            "già chiaramente da scartare: nessuna analisi cambierebbe l'esito",
        )
    if value <= 0:
        return Voi(
            value, gain, cost_of_analysis, False, "l'esito non dipende da ciò che l'analisi scoprirebbe"
        )
    if gain > cost_of_analysis * margin:
        return Voi(
            value, gain, cost_of_analysis, True, "caso al confine: l'informazione può cambiare la decisione"
        )
    return Voi(value, gain, cost_of_analysis, False, "il valore atteso dell'informazione non copre il costo")
