"""Receipt authenticity: a signed link and QR code that anyone can open to confirm a receipt is real.

The link is ``/verify/<reference>/<signature>/``. The signature is an HMAC of the reference with the
server's secret key, so a receipt cannot be forged or its reference guessed: someone holding only a
reference number cannot build a working link. (Rotating ``SECRET_KEY`` invalidates old links.)
"""
from __future__ import annotations

import hashlib
import hmac
import io

import segno
from django.conf import settings
from django.urls import reverse

QR_CID = "ipt-receipt-qr"
_SIGNATURE_LENGTH = 24  # hex characters (96 bits)


def _signature(reference: str) -> str:
    key = settings.SECRET_KEY.encode()
    return hmac.new(key, f"receipt-verify:{reference}".encode(), hashlib.sha256).hexdigest()[:_SIGNATURE_LENGTH]


def signature_is_valid(reference: str, signature: str) -> bool:
    return bool(reference) and hmac.compare_digest(_signature(reference), signature or "")


def verification_path(payment) -> str:
    return reverse("receipt-verify", args=[payment.reference_id, _signature(payment.reference_id)])


def verification_url(payment) -> str:
    return f"{settings.SITE_URL}{verification_path(payment)}"


def qr_png(url: str, *, scale: int = 6) -> bytes:
    """The QR code for ``url`` as PNG bytes (error correction M, quiet zone included)."""
    buffer = io.BytesIO()
    segno.make(url, error="m").save(buffer, kind="png", scale=scale, border=2, dark="#191C1D", light="#FFFFFF")
    return buffer.getvalue()


def mask_name(full_name: str) -> str:
    """'Asha Juma' -> 'A*** J***': enough for the owner to recognise, not enough to identify."""
    parts = [p for p in (full_name or "").replace("@", " ").split() if p]
    return " ".join(p[0].upper() + "***" for p in parts[:3]) or "-"
