"""Category plugins: what to look at for each kind of item.

A plugin lists the key attributes, the defects that matter, the photos an inspection needs, the
labels that apply and the hints for authenticity and comparables. Clothing, footwear and bags and
accessories exist; any other category falls back to the generic plugin, which says so (``covered``
is false) and lowers the confidence of everything derived from it. New categories (electronics,
collectibles, books, home, children, cosmetics) are new plugins, not changes to the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CategoryPlugin:
    key: str
    label: str
    covered: bool  # False: generic analysis, reduced confidence
    key_attributes: tuple[str, ...]
    defect_kinds: tuple[str, ...]
    # Photo roles an inspection needs, with their weight in the coverage score (they add up to 1).
    required_roles: tuple[tuple[str, float], ...]
    label_types: tuple[str, ...]
    authenticity_hints: tuple[str, ...]
    comparable_keys: tuple[str, ...]
    confidence_factor: float = 1.0

    def role_weights(self) -> dict[str, float]:
        return dict(self.required_roles)


# What to ask the seller for when a role is missing (Italian, imperative).
ROLE_REQUEST = {
    "front": "foto frontale dell'articolo intero",
    "back": "foto del retro",
    "label": "foto dell'etichetta interna (marca e taglia)",
    "care_label": "foto dell'etichetta di lavaggio e composizione",
    "detail": "foto ravvicinate dei dettagli (colletto, polsini, cuciture)",
    "sole": "foto della suola",
    "inside": "foto dell'interno",
    "worn": "foto indossato",
}

CLOTHING = CategoryPlugin(
    key="clothing",
    label="Abbigliamento",
    covered=True,
    key_attributes=("brand", "model", "size", "color", "material", "gender", "fit"),
    defect_kinds=(
        "stain",
        "halo",
        "hole",
        "tear",
        "fading",
        "pilling",
        "snag",
        "seam",
        "deformation",
        "collar_cuffs",
        "print_damage",
        "wear",
    ),
    required_roles=(("front", 0.25), ("back", 0.20), ("label", 0.25), ("care_label", 0.15), ("detail", 0.15)),
    label_types=("brand_label", "care_label", "composition", "size_label", "sku_code", "paper_tag"),
    authenticity_hints=(
        "logo e ricamo",
        "font e layout dell'etichetta",
        "codice RN/CA",
        "cuciture",
        "zip e bottoni",
    ),
    comparable_keys=("brand", "model", "size", "condition", "color"),
)
FOOTWEAR = CategoryPlugin(
    key="footwear",
    label="Scarpe",
    covered=True,
    key_attributes=("brand", "model", "size", "color", "material", "gender"),
    defect_kinds=(
        "sole_wear",
        "heel_wear",
        "creasing",
        "scuff",
        "stain",
        "tear",
        "seam",
        "wear",
        "print_damage",
    ),
    required_roles=(("front", 0.25), ("sole", 0.25), ("inside", 0.20), ("label", 0.15), ("detail", 0.15)),
    label_types=("brand_label", "size_label", "sku_code", "barcode", "paper_tag"),
    authenticity_hints=(
        "codice interno (SKU)",
        "suola e incisioni",
        "cuciture",
        "scatola e etichetta della scatola",
    ),
    comparable_keys=("brand", "model", "size", "condition", "color"),
)
BAGS_ACCESSORIES = CategoryPlugin(
    key="bags_accessories",
    label="Borse e accessori",
    covered=True,
    key_attributes=("brand", "model", "color", "material", "hardware"),
    defect_kinds=("scratch", "corner_wear", "zipper", "lining_stain", "handle_wear", "stain", "tear", "wear"),
    required_roles=(("front", 0.25), ("back", 0.15), ("inside", 0.25), ("label", 0.20), ("detail", 0.15)),
    label_types=("brand_label", "sku_code", "barcode", "paper_tag", "proof_of_purchase"),
    authenticity_hints=(
        "punzonatura e seriale",
        "ferramenta",
        "cuciture",
        "fodera",
        "dust bag e certificato",
    ),
    comparable_keys=("brand", "model", "color", "condition"),
)
GENERIC = CategoryPlugin(
    key="generic",
    label="Categoria generica",
    covered=False,
    key_attributes=("brand", "model", "color"),
    defect_kinds=("stain", "tear", "scratch", "wear", "other"),
    required_roles=(("front", 0.5), ("detail", 0.5)),
    label_types=("brand_label", "sku_code", "barcode"),
    authenticity_hints=(),
    comparable_keys=("brand", "model", "condition"),
    confidence_factor=0.7,
)

_CLOTHING_PARENTS = {"tops", "outerwear", "bottoms"}


def plugin_for(category_slug: str | None, parent_slug: str | None = None) -> CategoryPlugin:
    """The plugin for a category slug (and its parent); the generic one when none fits."""
    for slug in (category_slug, parent_slug):
        if slug in _CLOTHING_PARENTS:
            return CLOTHING
        if slug == "footwear":
            return FOOTWEAR
    if category_slug in {"bags"}:
        return BAGS_ACCESSORIES
    if parent_slug == "accessories" or category_slug == "accessories":
        # Belts, scarves and caps share the checks of bags and accessories (hardware, labels, wear).
        return BAGS_ACCESSORIES
    if category_slug in {
        "hoodies",
        "sweatshirts",
        "polo-shirts",
        "t-shirts",
        "shirts",
        "knitwear",
        "football-shirts",
        "jackets",
        "coats",
        "puffer-jackets",
        "fleece",
        "jeans",
        "trousers",
        "tracksuits",
    }:
        return CLOTHING
    if category_slug == "sneakers":
        return FOOTWEAR
    return GENERIC
