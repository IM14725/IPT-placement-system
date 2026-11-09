import base64
import hashlib
import hmac
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from django.conf import settings

logger = logging.getLogger("security")

DEFAULT_APPLICATION_FEE = Decimal("15000.00")
DEFAULT_WEBHOOK_MAX_AGE_SECONDS = 300


def get_application_fee() -> Decimal:
    """Return the canonical application fee (TZS) from PlatformSetting or settings."""
    try:
        from apps.core.models import PlatformSetting

        row = PlatformSetting.objects.filter(key="app_fee_amount").only("value_number").first()
        if row and row.value_number is not None and float(row.value_number) > 0:
            return Decimal(str(row.value_number))
    except Exception:  # noqa: BLE001
        pass
    configured = getattr(settings, "APPLICATION_FEE", None)
    if configured is not None:
        try:
            return Decimal(str(configured))
        except (InvalidOperation, ValueError):
            pass
    return DEFAULT_APPLICATION_FEE


def _is_timestamp_fresh(incoming_timestamp: str, max_age_seconds: int | None = None) -> bool:
    """Verify the webhook timestamp is within ``max_age_seconds`` of current UTC time."""
    if max_age_seconds is None:
        max_age_seconds = int(
            getattr(settings, "SELCOM_WEBHOOK_MAX_AGE_SECONDS", DEFAULT_WEBHOOK_MAX_AGE_SECONDS)
        )
    if max_age_seconds <= 0:
        return True
    raw = (incoming_timestamp or "").strip()
    if not raw:
        return False
    try:
        ts = float(raw)
    except ValueError:
        try:
            iso = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ts = dt.timestamp()
        except ValueError:
            return False
    return abs(time.time() - ts) <= max_age_seconds


def send_selcom_wallet_pull(transaction_id, phone_number, amount):
    """
    Sends a Direct USSD push payload targeting Selcom's checkout workflow.
    """
    base_url = getattr(settings, "SELCOM_BASE_URL", "https://apitest.selcommobile.com")
    api_key = getattr(settings, "SELCOM_API_KEY", "MOCK_API_KEY")
    api_secret = getattr(settings, "SELCOM_API_SECRET", "MOCK_API_SECRET")
    vendor_till = getattr(settings, "SELCOM_VENDOR_TILL", "MOCK_TILL")

    payload = {
        "vendor": vendor_till,
        "order_id": str(transaction_id),
        "buyer_phone": phone_number,
        "amount": int(Decimal(str(amount))),  # Selcom expects integers for TZS amounts
        "currency": "TZS",
        "no_of_items": 1,
        "no_redirection": True,  # Instructs Selcom to execute a background USSD push directly
    }

    try:
        if api_key == "MOCK_API_KEY":
            if not getattr(settings, "DEBUG", False):
                return 500, {
                    "result": "FAIL",
                    "message": "Mock payment key is not permitted outside DEBUG mode.",
                }
            return 200, {
                "result": "SUCCESS",
                "transid": "MOCK-INITIATED-123",
                "message": "Mock prompt initiated.",
            }

        from selcom_apigw_client import apigwClient

        client = apigwClient.Client(base_url, api_key, api_secret)
        response_data = client.postFunc("/v1/checkout/create-order-minimal", payload)

        http_code = (
            200
            if response_data.get("resultcode") == "000"
            or response_data.get("result") == "SUCCESS"
            else 400
        )
        return http_code, response_data
    except Exception:  # noqa: BLE001
        logger.exception("Selcom wallet pull failed for order_id=%s", transaction_id)
        return 500, {
            "result": "FAIL",
            "message": "Payment gateway communication error.",
        }


def verify_selcom_webhook_signature(raw_request_body, incoming_digest, incoming_timestamp):
    """
    Validates if the incoming payload header digests match our local calculation
    and that the timestamp is fresh (anti-replay).
    """
    if not incoming_digest or not incoming_timestamp:
        return False

    api_secret = getattr(settings, "SELCOM_API_SECRET", "")
    if not api_secret or (
        not getattr(settings, "DEBUG", False) and api_secret == "MOCK_API_SECRET"
    ):
        return False

    if not _is_timestamp_fresh(str(incoming_timestamp)):
        return False

    # 1. Recreate the string base that Selcom hashed.
    message_to_sign = f"timestamp={incoming_timestamp}&body={raw_request_body.decode('utf-8')}"

    # 2. Compute HMAC-SHA256 hash using your API Secret key
    secret_bytes = api_secret.encode("utf-8")
    message_bytes = message_to_sign.encode("utf-8")

    computed_hmac = hmac.new(
        secret_bytes,
        msg=message_bytes,
        digestmod=hashlib.sha256,
    ).digest()

    # 3. Encode to Base64
    computed_digest = base64.b64encode(computed_hmac).decode("utf-8")

    # 4. Use a time-constant string comparison to safely neutralize timing attacks
    return hmac.compare_digest(computed_digest, incoming_digest)
