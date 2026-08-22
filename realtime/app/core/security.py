import hashlib
import hmac

from app.core.config import settings


def canonical(reference_id: str, status: str, gateway_txn_id: str, amount: str) -> str:
    return f"{reference_id}|{status}|{gateway_txn_id}|{amount}"


def sign(payload_str: str) -> str:
    return hmac.new(
        settings.gateway_webhook_secret.encode(), payload_str.encode(), hashlib.sha256
    ).hexdigest()


def verify(payload_str: str, provided: str | None) -> bool:
    if not provided:
        return False
    expected = sign(payload_str)
    return hmac.compare_digest(expected, provided)