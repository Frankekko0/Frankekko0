"""Generate a VAPID key pair for Web Push.

Usage: ``python -m app.tools.vapid`` and copy the two printed lines into your ``.env``.
Keys are printed once and never written to disk or logs.
"""

from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid01
from py_vapid.utils import b64urlencode


def generate() -> tuple[str, str]:
    """Return (public application-server key, private key), both base64url as browsers expect."""
    vapid = Vapid01()
    vapid.generate_keys()
    public = vapid.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    private = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
    return b64urlencode(public), b64urlencode(private)


if __name__ == "__main__":
    public_key, private_key = generate()
    print(f"VAPID_PUBLIC_KEY={public_key}")
    print(f"VAPID_PRIVATE_KEY={private_key}")
