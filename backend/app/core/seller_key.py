"""Opaque, server-protected seller keys.

FlipFinder keeps only a seller's average rating and number of reviews. To tell that two listings
come from the same seller (reposts, recycled photos, the same item in several sizes) it needs a
stable key, but never the member id or name. The extension sends a one-way hash of the member id;
because Vinted member ids are small numbers, that hash alone could be reversed by trying them all.
The server therefore wraps it in an HMAC with a secret that never leaves the server
(``SELLER_KEY_SECRET``; without it, one derived from ``JWT_SECRET``): whoever obtains the database
cannot recover the member from the key.
"""

from __future__ import annotations

import hashlib
import hmac

PREFIX = "s2:"
KEY_HEX_CHARS = 32  # 128 bits


def derived_secret(jwt_secret: str) -> bytes:
    """Secret used when ``SELLER_KEY_SECRET`` is not set. Rotating ``JWT_SECRET`` then changes
    every key: set ``SELLER_KEY_SECRET`` explicitly to keep them stable."""
    return hmac.new(jwt_secret.encode(), b"flipfinder:seller-key:v1", hashlib.sha256).digest()


def protect(raw: str, secret: bytes) -> str:
    """The stored key for a client-side key. Idempotent: an already protected key is returned as is."""
    if raw.startswith(PREFIX):
        return raw
    return PREFIX + hmac.new(secret, raw.encode(), hashlib.sha256).hexdigest()[:KEY_HEX_CHARS]
