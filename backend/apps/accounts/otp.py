"""One-time codes: issue, deliver, verify.

Codes are 6 digits, valid 5 minutes, single use, stored only as a keyed hash, limited to
5 guesses, and sending is rate limited. Issuing a new code invalidates the previous one.
Delivery is synchronous on purpose: queueing the code would put it on the Celery broker.
"""

import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import OneTimeCode, OtpChannel, OtpPurpose
from apps.core.providers import send_email, send_sms
from apps.core.rate_limit import consume

OTP_TTL = timedelta(minutes=5)
OTP_MAX_ATTEMPTS = 5
# Resend limit: a burst of 3, then one more per minute.
OTP_SEND_CAPACITY = 3
OTP_SEND_REFILL_PER_SECOND = 1 / 60

OK = "ok"
INVALID = "invalid"
EXPIRED = "expired"  # expired, already used, never issued, or out of attempts


class SendResult:
    __slots__ = ("sent", "retry_after")

    def __init__(self, sent, retry_after=0):
        self.sent = sent
        self.retry_after = retry_after


def _hash(otp_id, purpose, code):
    msg = f"{otp_id}:{purpose}:{code}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


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
    with transaction.atomic():
        OneTimeCode.objects.filter(
            user=user, purpose=purpose, consumed_at__isnull=True
        ).update(consumed_at=now)
        otp = OneTimeCode(
            user=user,
            purpose=purpose,
            channel=channel,
            destination=destination,
            expires_at=now + OTP_TTL,
        )
        otp.code_hash = _hash(otp.id, purpose, code)
        otp.save()

    minutes = int(OTP_TTL.total_seconds() // 60)
    text = f"Your IPT Marketplace verification code is {code}. It expires in {minutes} minutes."
    try:
        if channel == OtpChannel.EMAIL:
            send_email("Your IPT Marketplace verification code", [destination], text)
        else:
            send_sms(destination, text)
    except Exception:  # noqa: BLE001 - provider outage must not leak or crash the flow
        return SendResult(False)
    return SendResult(True)


def invalidate_pending(user, purpose):
    """Void every unused code for ``purpose`` (e.g. recovery codes once the user logs in)."""
    return OneTimeCode.objects.filter(
        user=user, purpose=purpose, consumed_at__isnull=True
    ).update(consumed_at=timezone.now())


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
        return EXPIRED
    otp.attempts += 1
    if hmac.compare_digest(otp.code_hash, _hash(otp.id, purpose, (code or "").strip())):
        otp.consumed_at = now
        otp.save(update_fields=["attempts", "consumed_at"])
        return OK
    if otp.attempts >= OTP_MAX_ATTEMPTS:
        otp.consumed_at = now
        otp.save(update_fields=["attempts", "consumed_at"])
        return EXPIRED
    otp.save(update_fields=["attempts"])
    return INVALID
