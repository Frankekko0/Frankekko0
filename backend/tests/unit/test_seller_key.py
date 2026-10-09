"""Seller keys: opaque, stable, and not recoverable from the database alone."""

import hashlib

from app.core.seller_key import PREFIX, derived_secret, protect

SECRET = b"server-only-secret"


def client_key(member_id: int) -> str:
    # What the extension sends: a one-way hash of the member id (no secret).
    return "h:" + hashlib.sha256(f"vinted-member:{member_id}".encode()).hexdigest()[:24]


def test_the_same_seller_always_gets_the_same_key() -> None:
    assert protect(client_key(12345), SECRET) == protect(client_key(12345), SECRET)
    assert protect(client_key(12345), SECRET) != protect(client_key(12346), SECRET)


def test_the_stored_key_cannot_be_matched_without_the_secret() -> None:
    stored = protect(client_key(12345), SECRET)
    assert stored.startswith(PREFIX) and len(stored) == len(PREFIX) + 32
    # Trying every member id with the client-side hash (all an attacker with only the database
    # can do) finds nothing: the key depends on the server secret.
    assert all(protect(client_key(i), b"wrong") != stored for i in range(0, 20000))
    assert protect(client_key(12345), b"wrong") != stored


def test_protecting_twice_changes_nothing() -> None:
    once = protect(client_key(7), SECRET)
    assert protect(once, SECRET) == once


def test_the_fallback_secret_comes_from_the_jwt_secret_and_differs_with_it() -> None:
    assert derived_secret("a" * 40) == derived_secret("a" * 40)
    assert derived_secret("a" * 40) != derived_secret("b" * 40)
