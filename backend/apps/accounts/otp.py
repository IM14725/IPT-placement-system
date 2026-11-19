"""One-time codes: issue, deliver, verify.

Codes are 6 digits, valid 5 minutes, single use, stored only as a keyed hash, limited to
5 guesses, and sending is rate limited. Issuing a new code invalidates the previous one.
Delivery is synchronous on purpose: queueing the code would put it on the Celery broker.
"""

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import OneTimeCode, OtpChannel, OtpDelivery, OtpOutcome, OtpPurpose
from apps.core.providers import send_email, send_sms
from apps.core.rate_limit import consume

log = logging.getLogger(__name__)

ADMIN_ROLES = ("ADMIN", "SUPER_ADMIN")

OTP_TTL = timedelta(minutes=5)
OTP_MAX_ATTEMPTS = 5
# Resend limit: a burst of 3, then one more per minute.
OTP_SEND_CAPACITY = 3
OTP_SEND_REFILL_PER_SECOND = 1 / 60

OK = "ok"
INVALID = "invalid"
EXPIRED = "expired"  # expired, already used, never issued, or out of attempts


class SendResult:
    """``code_kept``: delivery failed but the code is still valid, so support can relay it."""

    __slots__ = ("sent", "retry_after", "code_kept")

    def __init__(self, sent, retry_after=0, code_kept=False):
        self.sent = sent
        self.retry_after = retry_after
        self.code_kept = code_kept


def _hash(otp_id, purpose, code):
    msg = f"{otp_id}:{purpose}:{code}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


def _fernet():
    import base64

    from cryptography.fernet import Fernet

    key = base64.urlsafe_b64encode(hashlib.sha256(b"otp-code:" + settings.SECRET_KEY.encode()).digest())
    return Fernet(key)


def _seal(code, user):
    """Encrypt ``code`` for the admin "View code" action. Nothing is kept when the switch is off, nor for
    administrator accounts (an admin must never be able to read another admin's code)."""
    if not getattr(settings, "OTP_ADMIN_CAN_VIEW_CODES", False) or user.role in ADMIN_ROLES:
        return ""
    return _fernet().encrypt(code.encode()).decode()


def unseal(otp):
    """The plain code of ``otp``, or None if it was not kept or can no longer be used."""
    if not otp.code_enc or not otp.is_live:
        return None
    try:
        return _fernet().decrypt(otp.code_enc.encode()).decode()
    except Exception:  # noqa: BLE001 - SECRET_KEY rotated: treat as not available
        return None


def destination_for(user, channel):
    return user.email if channel == OtpChannel.EMAIL else user.phone


def channel_verified(user, channel):
    """Recovery may only use a contact method already verified on the account."""
    if channel == OtpChannel.EMAIL:
        return bool(user.email and user.email_verified)
    return bool(user.phone and user.phone_verified)


def send_otp(user, purpose, channel, destination=None):
    if getattr(settings, "RATE_LIMIT_ENABLED", True):
        rl = consume(
            "otp-send", f"{user.pk}:{purpose}", OTP_SEND_CAPACITY, OTP_SEND_REFILL_PER_SECOND
        )
        if not rl.allowed:
            return SendResult(False, retry_after=max(1, int(rl.retry_after)))

    destination = destination or destination_for(user, channel)
    if not destination:
        return SendResult(False)

    code = f"{secrets.randbelow(10**6):06d}"
    now = timezone.now()
    wipe_expired_codes()
    with transaction.atomic():
        OneTimeCode.objects.filter(
            user=user, purpose=purpose, consumed_at__isnull=True
        ).update(consumed_at=now, outcome=OtpOutcome.SUPERSEDED, code_enc="")
        otp = OneTimeCode(
            user=user,
            purpose=purpose,
            channel=channel,
            destination=destination,
            expires_at=now + OTP_TTL,
        )
        otp.code_hash = _hash(otp.id, purpose, code)
        otp.code_enc = _seal(code, user)
        otp.save()

    minutes = int(OTP_TTL.total_seconds() // 60)
    text = f"Your IPT Marketplace verification code is {code}. It expires in {minutes} minutes."
    try:
        if channel == OtpChannel.EMAIL:
            delivered = send_email("Your IPT Marketplace verification code", [destination], text)
        else:
            delivered = send_sms(destination, text, kind="otp", idempotency_key=f"otp-{otp.id}")
    except Exception as exc:  # noqa: BLE001 - provider outage must not leak or crash the flow
        # Never log ``text`` (it holds the code). The exception type and provider message are enough.
        log.warning("OTP delivery failed (%s, %s): %s: %s", purpose, channel, type(exc).__name__, exc)
        failure = {
            "delivery_status": OtpDelivery.FAILED,
            "delivery_error": f"{type(exc).__name__}: {exc}"[:200],
        }
        if otp.code_enc:
            # The code stays valid: support can read it from the admin "OTP requests" page and relay it.
            OneTimeCode.objects.filter(pk=otp.pk).update(**failure)
            return SendResult(False, code_kept=True)
        # Nothing was kept (switch off, or an administrator account): nobody could ever read this code.
        OneTimeCode.objects.filter(pk=otp.pk).update(
            **failure, outcome=OtpOutcome.UNDELIVERED, consumed_at=timezone.now()
        )
        return SendResult(False)
    provider = str((delivered or {}).get("provider", ""))[:30]
    OneTimeCode.objects.filter(pk=otp.pk).update(delivery_status=OtpDelivery.SENT, delivery_provider=provider)
    return SendResult(True)


def wipe_expired_codes():
    """Expired codes lose their ciphertext (also run every minute by the ``accounts.wipe_expired_otp_codes`` task)."""
    return OneTimeCode.objects.filter(expires_at__lte=timezone.now()).exclude(code_enc="").update(code_enc="")


def invalidate_pending(user, purpose):
    """Void every unused code for ``purpose`` (e.g. recovery codes once the user logs in)."""
    return OneTimeCode.objects.filter(
        user=user, purpose=purpose, consumed_at__isnull=True
    ).update(consumed_at=timezone.now(), outcome=OtpOutcome.REVOKED, code_enc="")


def pending_destination(user, purpose):
    """Where the live (unused, unexpired) code for ``purpose`` was sent, e.g. the NEW email."""
    otp = (
        OneTimeCode.objects.filter(
            user=user, purpose=purpose, consumed_at__isnull=True, expires_at__gt=timezone.now()
        )
        .order_by("-created_at")
        .first()
    )
    return otp.destination if otp else None


@transaction.atomic
def verify_otp(user, purpose, code):
    now = timezone.now()
    otp = (
        OneTimeCode.objects.select_for_update()
        .filter(user=user, purpose=purpose, consumed_at__isnull=True)
        .order_by("-created_at")
        .first()
    )
    if otp is None or otp.expires_at <= now:
        if otp is not None and otp.code_enc:
            otp.code_enc = ""
            otp.save(update_fields=["code_enc"])
        return EXPIRED
    otp.attempts += 1
    if hmac.compare_digest(otp.code_hash, _hash(otp.id, purpose, (code or "").strip())):
        otp.consumed_at = now
        otp.outcome = OtpOutcome.VERIFIED
        otp.code_enc = ""
        otp.save(update_fields=["attempts", "consumed_at", "outcome", "code_enc"])
        return OK
    if otp.attempts >= OTP_MAX_ATTEMPTS:
        otp.consumed_at = now
        otp.outcome = OtpOutcome.LOCKED
        otp.code_enc = ""
        otp.save(update_fields=["attempts", "consumed_at", "outcome", "code_enc"])
        return EXPIRED
    otp.save(update_fields=["attempts"])
    return INVALID


def revoke(otp):
    """Void one pending code (admin action). Returns True if it was still live."""
    return bool(
        OneTimeCode.objects.filter(pk=otp.pk, consumed_at__isnull=True).update(
            consumed_at=timezone.now(), outcome=OtpOutcome.REVOKED, code_enc=""
        )
    )
