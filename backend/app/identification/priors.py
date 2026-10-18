"""The prior probability that an item of a brand is a counterfeit, when the brand is not (fully) known.

A brand the catalogue knows carries its own prior (``BrandSpec.counterfeit_risk``). A brand it does not know is
*unknown*, not "safe": the doubt about authenticity is raised for it (``UNKNOWN_BRAND_RISK``, above the 0.1 that
used to be assumed), and a brand the text only suggests (``brand_guess``) brings its own, higher prior with it:
a listing that may be a Louis Vuitton bag is judged as one until the label proves otherwise.
"""

from __future__ import annotations

from typing import Any

from app.identification.taxonomy import DEFAULT_TAXONOMY

UNKNOWN_BRAND_RISK = 0.2


def counterfeit_prior(brand_risk: float | None, identification: dict[str, Any] | None) -> float:
    """``brand_risk``: the stored brand's prior (``None``: no brand row). ``identification``: the stored result of
    the identification engine (``IdentificationResult.as_dict()``), whose ``brand_guess`` may raise the prior."""
    if brand_risk is not None:
        return brand_risk
    risk = UNKNOWN_BRAND_RISK
    guess = (identification or {}).get("brand_guess") or {}
    spec = DEFAULT_TAXONOMY.brand_by_slug.get(guess.get("value") or "")
    if spec is not None:
        risk = max(risk, float(spec.counterfeit_risk))
    return risk
