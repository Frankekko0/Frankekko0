"""Several items sold together: "Lotto 3 felpe", "Set 2 polo", "Completo giacca e pantaloni", "10 pezzi".

A price for a bundle is not the price of one item: the single-item comparables say nothing about it, and the brand
field of a bundle may name only one of the brands in it. Pure, on the folded text of a listing.
"""

from __future__ import annotations

import re

_BUNDLE = (
    re.compile(
        r"\b(?:lotto|stock|bundle|(?<!a )lot of|lot de|posten|paquete|pack)\b(?:\s+(?:di|of|de|da))?\s*(\d{1,2})?\b"
    ),
    re.compile(
        r"\bset\s+(?:di\s+)?(?:(\d{1,2})\b|(?=polo|felpe|t-shirt|magliette|camicie|maglie|calzini|giacche))"
    ),
    re.compile(r"\bcompleto\s+(?:giacca|pantalon[ei]|tuta|sportivo|due pezzi|2 pezzi)\b"),
    re.compile(r"\bconjunto\b"),
)
_PIECES = re.compile(r"\b(\d{1,2})\s*(?:pezzi|pz|pcs|pieces|capi|stuck|piezas|articoli|items)\b")
_WORD_NUMBERS = {"due": 2, "tre": 3, "quattro": 4, "cinque": 5, "two": 2, "three": 3, "four": 4, "five": 5}
_WORD_PIECES = re.compile(
    r"\b(due|tre|quattro|cinque|two|three|four|five)\s+(?:pezzi|capi|polo|felpe|t-shirt|magliette|camicie|jeans|"
    r"pantaloni|giacche|paia|pieces|shirts|hoodies)\b"
)
def lot_pieces(text: str) -> int | None:
    """How many pieces the text says are sold together (``None``: one item, or not said)."""
    for rx in _BUNDLE:
        if m := rx.search(text):
            n = m.group(1) if m.lastindex else None
            return int(n) if n and int(n) > 1 else 2
    if m := _PIECES.search(text):
        return int(m.group(1)) if int(m.group(1)) > 1 else None
    if m := _WORD_PIECES.search(text):
        return _WORD_NUMBERS[m.group(1)]
    return None


