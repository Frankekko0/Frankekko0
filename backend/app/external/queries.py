"""The queries sent for one model (each query = 1 credit).

1. Google Shopping ``"<brand> <model>"``: new prices from retailers, used offers from marketplaces;
2. Google web search ``"<brand> <model> usato prezzo"`` restricted to the second-hand marketplaces
   (``site:`` operators): asking prices and, where the page says so, concluded sales;
3. optional, only when the first two found the model and the budget allows: a search for concluded
   sales (``venduto``/``sold``) on the marketplaces that publish them.

Vinted is never searched: it is FlipFinder's own source.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.external.parse import SECOND_HAND_SITES

# Marketplaces whose pages state that an item was sold (with the date).
SOLD_SITES = ("ebay.it", "ebay.com", "vestiairecollective.com", "grailed.com")


@dataclass(frozen=True)
class SearchQuery:
    endpoint: str  # shopping | search
    purpose: str  # shopping | used | sold
    q: str


def _sites(sites: tuple[str, ...]) -> str:
    return "(" + " OR ".join(f"site:{s}" for s in sites) + ")"


def _name(brand_name: str, model_name: str) -> str:
    brand, model = " ".join(brand_name.split()), " ".join(model_name.split())
    # "Levi's" + "Levi's 501" -> no repeated brand.
    return model if model.lower().startswith(brand.lower()) else f"{brand} {model}"


def model_queries(brand_name: str, model_name: str) -> list[SearchQuery]:
    """The two queries always sent for a model."""
    name = _name(brand_name, model_name)
    return [
        SearchQuery("shopping", "shopping", name),
        SearchQuery("search", "used", f"{name} usato prezzo {_sites(SECOND_HAND_SITES)}"),
    ]


def sold_query(brand_name: str, model_name: str) -> SearchQuery:
    """The optional third query (concluded sales)."""
    name = _name(brand_name, model_name)
    return SearchQuery("search", "sold", f"{name} (venduto OR sold) {_sites(SOLD_SITES)}")
