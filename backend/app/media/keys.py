"""Stable identity of a listing photo before its bytes are known.

Vinted serves the same photo from several hosts with signed query strings; the path identifies
it. The key is used to compare photo sets across observations. Once a copy exists, its SHA-256
(``ListingImage.sha256``) is the stronger identity.
"""

from __future__ import annotations

import hashlib
from urllib.parse import urlparse


def image_key(url: str) -> str:
    """16 hex chars, from the path only (no host, query or fragment)."""
    path = urlparse(url).path or url
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]


def image_keys(urls: list[str]) -> list[str]:
    """Keys in the original order, duplicates removed (first occurrence wins)."""
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        k = image_key(u)
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out
