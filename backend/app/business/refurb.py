"""Refurbishing: buy an item "to fix" when the return after the repair convinces, not on the price alone.

For each recoverable defect there is a materials cost, the labour (minutes valued at the user's own hourly
value) and a share of the clean resale price recovered with a probability of success. The comparison is the
ROI *after* restoration against the ROI of selling it as it is. The uplift ratios are assumptions (listed in
docs/LIMITATIONS.md) that the user can override with figures from their own repairs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

# defect -> (materials €, minutes, share of the clean price recovered, probability it works)
REMEDIES: dict[str, tuple[float, float, float, float]] = {
    "pilling": (0.30, 10, 0.12, 0.90),
    "stain": (2.00, 15, 0.22, 0.65),
    "wrinkles": (0.10, 10, 0.05, 0.95),
    "small_repair": (3.00, 30, 0.20, 0.80),
    "odor": (1.50, 20, 0.10, 0.70),
}
HOURLY_VALUE = 8.0


@dataclass(frozen=True)
class RefurbResult:
    cost_as_is: float
    cost_after: float
    resale_as_is: float
    resale_after: float
    profit_as_is: float
    profit_after: float
    roi_as_is: float | None
    roi_after: float | None
    worth_it: bool
    materials: float
    labour: float
    steps: list[str] = field(default_factory=list)
    reason: str = ""

    def as_dict(self) -> dict[str, object]:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def evaluate(
    *,
    purchase_cost: float,
    resale_clean: float,
    defects: list[str],
    net_of_price: Callable[[float], float],
    resale_as_is: float | None = None,
    min_roi: float = 0.4,
    hourly_value: float = HOURLY_VALUE,
) -> RefurbResult:
    unknown = [d for d in defects if d not in REMEDIES]
    known = [d for d in defects if d in REMEDIES]
    materials = sum(REMEDIES[d][0] for d in known)
    minutes = sum(REMEDIES[d][1] for d in known)
    labour = minutes / 60.0 * hourly_value
    recovered = sum(REMEDIES[d][2] * REMEDIES[d][3] for d in known)
    as_is = (
        resale_as_is
        if resale_as_is is not None
        else resale_clean * (1 - min(0.6, sum(REMEDIES[d][2] for d in known)))
    )
    after = min(resale_clean, as_is + resale_clean * recovered)
    cost_after = purchase_cost + materials + labour
    p_as, p_after = net_of_price(as_is) - purchase_cost, net_of_price(after) - cost_after
    roi_as = p_as / purchase_cost if purchase_cost > 0 else None
    roi_after = p_after / cost_after if cost_after > 0 else None
    gain = net_of_price(after) - net_of_price(as_is)
    worth = (
        bool(known)
        and not unknown
        and roi_after is not None
        and roi_after >= min_roi
        and gain >= (materials + labour) * 1.5
    )
    if unknown:
        reason = f"difetti senza rimedio noto: {', '.join(unknown)} (non valutati)"
    elif not known:
        reason = "nessun difetto da recuperare"
    elif worth and roi_as is not None and roi_after is not None and roi_after > roi_as:
        reason = f"dopo il ripristino il ROI sale da {roi_as:.0%} a {roi_after:.0%} e il guadagno copre materiali e lavoro"
    elif worth and roi_after is not None:
        reason = f"il ROI dopo il ripristino ({roi_after:.0%}) rispetta il minimo e il guadagno in più ({gain:.2f} €) copre materiali e lavoro"
    elif roi_after is not None and roi_after < min_roi:
        reason = f"anche dopo il ripristino il ROI ({roi_after:.0%}) resta sotto il minimo"
    else:
        reason = "il ripristino costa quanto vale"
    steps = [f"{d}: {REMEDIES[d][1]:g} min, {REMEDIES[d][0]:.2f} € di materiali" for d in known]
    return RefurbResult(
        purchase_cost,
        cost_after,
        as_is,
        after,
        p_as,
        p_after,
        roi_as,
        roi_after,
        worth,
        materials,
        labour,
        steps,
        reason,
    )
