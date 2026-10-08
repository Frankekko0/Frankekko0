"""Shared key of a model for the external search cache and its price references."""

from __future__ import annotations

from app.identification.taxonomy import fold


def model_key(brand_slug: str, model_name: str) -> str:
    """``"<brand_slug>|<model folded>"``, e.g. ``"nike|air max 90"``: the same model always maps
    to the same cache row, whatever the capitalisation or accents of the title it came from."""
    return f"{brand_slug}|{fold(model_name)}"[:160]
