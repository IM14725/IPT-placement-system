"""Unforgeable public links: an HMAC of ``kind:value`` with the server's secret key.

Used for the receipt and acceptance-letter verification pages, so a link cannot be built from an id alone.
Rotating ``SECRET_KEY`` invalidates old links.
"""
import hashlib
import hmac

from django.conf import settings

LENGTH = 24  # hex characters (96 bits)


def sign(kind: str, value) -> str:
    key = settings.SECRET_KEY.encode()
    return hmac.new(key, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()[:LENGTH]


def is_valid(kind: str, value, signature: str) -> bool:
    return bool(value) and hmac.compare_digest(sign(kind, value), signature or "")
