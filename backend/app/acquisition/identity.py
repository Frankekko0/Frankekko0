"""Recognizing Vinted listings: one definition of "a Vinted link" and of the record key."""

from __future__ import annotations

import re

from app.ingestion.normalizer import title_fingerprint

# Vinted's own domains only (``vinted.it.example.com`` is not Vinted).
VINTED_TLDS = "it|fr|de|es|be|nl|lu|pt|at|pl|cz|sk|lt|co\\.uk|com|se|fi|dk|gr|hr|ro|hu|ie|si|lv|ee"
VINTED_HOST = re.compile(rf"^https?://(?:www\.)?vinted\.(?:{VINTED_TLDS})(?::\d+)?/", re.IGNORECASE)
VINTED_ID = re.compile(r"/items/(\d+)")
LINK = re.compile(r"https?://[^\s<>\"'()]+", re.IGNORECASE)


def listing_identity(url: str) -> tuple[str, str]:
    """``(provider, external_id)``: Vinted links map to the single ``vinted`` provider keyed by the
    Vinted item id (the same on every Vinted domain), anything else to ``manual``."""
    m = VINTED_ID.search(url)
    if m and VINTED_HOST.match(url):
        return "vinted", m.group(1)
    return "manual", (m.group(1) if m else title_fingerprint(url)[:24])


def vinted_links(text: str) -> list[tuple[str, str]]:
    """Every distinct Vinted item link in a text: ``[(clean url, vinted id)]``, in order."""
    out: dict[str, str] = {}
    for raw in LINK.findall(text or ""):
        url = raw.rstrip(".,;:!?»”")
        if not VINTED_HOST.match(url):
            continue
        m = VINTED_ID.search(url)
        if m and m.group(1) not in out:
            out[m.group(1)] = url.split("?")[0].split("#")[0]
    return [(u, vid) for vid, u in out.items()]


def title_from_slug(url: str) -> str:
    """``/items/4242-felpa-ralph-lauren-blu`` -> ``Felpa ralph lauren blu`` (link-only records)."""
    m = re.search(r"/items/\d+-([^/?#]+)", url)
    if not m:
        return "Annuncio Vinted"
    words = m.group(1).replace("-", " ").strip()
    return (words[:1].upper() + words[1:])[:300] or "Annuncio Vinted"
