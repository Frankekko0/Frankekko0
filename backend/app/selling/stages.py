"""Stages of an item after it is bought (§4.13) and the moves between them."""

from __future__ import annotations

STAGES = ("identified", "purchased", "arriving", "to_list", "listed", "sold", "returned", "unsold")

TRANSITIONS: dict[str, frozenset[str]] = {
    "identified": frozenset({"purchased"}),
    "purchased": frozenset({"arriving", "to_list"}),
    "arriving": frozenset({"to_list", "returned"}),
    "to_list": frozenset({"listed", "returned"}),
    "listed": frozenset({"sold", "returned", "unsold", "to_list"}),  # back to "to_list": taken down
    "unsold": frozenset({"listed", "to_list"}),
    "returned": frozenset({"to_list", "unsold"}),
    "sold": frozenset({"returned"}),  # a sale can come back
}

LABELS = {
    "identified": "Identificato",
    "purchased": "Acquistato",
    "arriving": "In arrivo",
    "to_list": "Da pubblicare",
    "listed": "Pubblicato",
    "sold": "Venduto",
    "returned": "Reso",
    "unsold": "Invenduto",
}

# Stages whose capital is still tied up in stock.
IN_STOCK = frozenset({"purchased", "arriving", "to_list", "listed", "unsold", "returned"})


class StageError(ValueError):
    pass


def can_move(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, frozenset())


def check_move(current: str, target: str) -> None:
    if current not in TRANSITIONS or target not in STAGES:
        raise StageError(f"stato sconosciuto: {current!r} → {target!r}")
    if not can_move(current, target):
        allowed = ", ".join(sorted(TRANSITIONS[current])) or "nessuno"
        raise StageError(f"da «{LABELS[current]}» non si passa a «{LABELS[target]}» (consentiti: {allowed})")
