"""OTP screens: email verification after registration, and password-recovery code entry."""

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, render

from apps.accounts import otp
from apps.accounts.forms import OtpCodeForm, RecoveryMethodForm
from apps.accounts.models import OtpChannel, OtpPurpose, User
from apps.core.rate_limit import consume, token_bucket

VERIFY_USER_KEY = "otp_register_user_id"
RESET_EMAIL_KEY = "pwd_reset_email"
RESET_CHANNEL_KEY = "pwd_reset_channel"

_EXPIRED_MSG = "That code has expired or can no longer be used. Request a new one."
_INVALID_MSG = "Incorrect code. Please try again."


def _ip_key(request):
    return f"ip:{request.META.get('REMOTE_ADDR', 'unknown')}"


def _mask_email(email):
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if domain else email


def begin_email_verification(request, user):
    """Registration sends the code to the email address only (no channel choice)."""
    request.session[VERIFY_USER_KEY] = str(user.id)
    otp.send_otp(user, OtpPurpose.REGISTER, OtpChannel.EMAIL)
    return redirect("verify-email")


DECOY_VERIFY = "decoy"


def begin_decoy_verification(request):
    """An existing address was 'registered': mirror the real flow without ever issuing a code."""
    request.session[VERIFY_USER_KEY] = DECOY_VERIFY
    return redirect("verify-email")


def _decoy_verify_email(request):
    from apps.accounts.views import DECOY_EMAIL_SESSION_KEY

    email = request.session.get(DECOY_EMAIL_SESSION_KEY)
    if not email:
        request.session.pop(VERIFY_USER_KEY, None)
        return redirect("login")
    form = OtpCodeForm(request.POST or None)
    if request.method == "POST":
        if request.POST.get("action") == "resend":
            # Same send limits and messages as a real code, so the limits can't tell the two apart.
            sent = True
            if getattr(settings, "RATE_LIMIT_ENABLED", True):
                rl = consume("otp-send", f"decoy:{email}", otp.OTP_SEND_CAPACITY, otp.OTP_SEND_REFILL_PER_SECOND)
                sent = rl.allowed
                result = otp.SendResult(sent, retry_after=0 if sent else max(1, int(rl.retry_after)))
            else:
                result = otp.SendResult(True)
            if sent:
                request.session["decoy_attempts"] = 0
            _notify_send_result(request, result)
            return redirect("verify-email")
        if form.is_valid():
            attempts = request.session.get("decoy_attempts", 0) + 1
            request.session["decoy_attempts"] = attempts
            # A real code locks after 5 wrong guesses; mirror that.
            form.add_error("code", _INVALID_MSG if attempts <= otp.OTP_MAX_ATTEMPTS else _EXPIRED_MSG)
    return render(
        request,
        "registration/otp_verify.html",
        {
            "form": form,
            "heading": "Verify your email",
            "intro": f"We sent a 6-digit code to {_mask_email(email)}. It expires in 5 minutes.",
            "submit_label": "Verify and continue",
        },
    )


def _notify_send_result(request, result):
    if result.sent:
        messages.success(request, "A new code has been sent.")
    elif result.retry_after:
        messages.error(request, f"Please wait {result.retry_after} seconds before requesting another code.")
    else:
        messages.error(request, "We could not send a code. Please try again shortly.")


@token_bucket(
    capacity=10, refill_per_second=1 / 30, scope="otp-verify", key_fn=_ip_key, methods=["POST"]
)
def verify_email(request):
    from apps.accounts.views import _start_session

    if request.session.get(VERIFY_USER_KEY) == DECOY_VERIFY:
        return _decoy_verify_email(request)
    user = User.objects.filter(pk=request.session.get(VERIFY_USER_KEY), is_active=True).first()
    if user is None:
        return redirect("login")
    form = OtpCodeForm(request.POST or None)
    if request.method == "POST":
        if request.POST.get("action") == "resend":
            _notify_send_result(request, otp.send_otp(user, OtpPurpose.REGISTER, OtpChannel.EMAIL))
            return redirect("verify-email")
        if form.is_valid():
            result = otp.verify_otp(user, OtpPurpose.REGISTER, form.cleaned_data["code"])
            if result == otp.OK:
                user.email_verified = True
                user.save(update_fields=["email_verified"])
                request.session.pop(VERIFY_USER_KEY, None)
                response, error = _start_session(request, user)
                if response is not None:
                    return response
                messages.error(request, error)
                return redirect("login")
            form.add_error("code", _INVALID_MSG if result == otp.INVALID else _EXPIRED_MSG)
    return render(
        request,
        "registration/otp_verify.html",
        {
            "form": form,
            "heading": "Verify your email",
            "intro": f"We sent a 6-digit code to {_mask_email(user.email)}. It expires in 5 minutes.",
            "submit_label": "Verify and continue",
        },
    )


@token_bucket(
    capacity=5, refill_per_second=1 / 60, scope="recovery-ip", key_fn=_ip_key, methods=["POST"]
)
def reset_method(request):
    """Recovery step 2: choose email or SMS. Both are always offered, so the screen reveals
    nothing about the account; a code is only sent to a method already verified on it."""
    email = request.session.get(RESET_EMAIL_KEY)
    if request.user.is_authenticated or not email:
        return redirect("forgot-password")
    form = RecoveryMethodForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        channel = form.cleaned_data["channel"]
        user = User.objects.filter(email=email, is_active=True).first()
        if user is not None and otp.channel_verified(user, channel):
            otp.send_otp(user, OtpPurpose.RECOVERY, channel)
        request.session[RESET_CHANNEL_KEY] = channel
        return redirect("reset-verify")
    return render(request, "registration/reset_method.html", {"form": form})


@token_bucket(
    capacity=10, refill_per_second=1 / 30, scope="otp-verify", key_fn=_ip_key, methods=["POST"]
)
def reset_verify(request):
    email = request.session.get(RESET_EMAIL_KEY)
    channel = request.session.get(RESET_CHANNEL_KEY)
    if request.user.is_authenticated or not email:
        return redirect("forgot-password")
    if channel not in (OtpChannel.EMAIL, OtpChannel.SMS):
        return redirect("reset-method")
    user = User.objects.filter(email=email, is_active=True).first()
    form = OtpCodeForm(request.POST or None)
    if request.method == "POST":
        if request.POST.get("action") == "resend":
            if user is not None and otp.channel_verified(user, channel):
                otp.send_otp(user, OtpPurpose.RECOVERY, channel)
            messages.success(request, "If a code can be sent, it is on its way.")
            return redirect("reset-verify")
        if form.is_valid():
            result = otp.EXPIRED
            if user is not None and otp.channel_verified(user, channel):
                result = otp.verify_otp(user, OtpPurpose.RECOVERY, form.cleaned_data["code"])
            if result == otp.OK:
                request.session.pop(RESET_EMAIL_KEY, None)
                request.session.pop(RESET_CHANNEL_KEY, None)
                request.session["pwd_reset_verified_user_id"] = str(user.id)
                return redirect("reset-password")
            form.add_error("code", _INVALID_MSG if result == otp.INVALID else _EXPIRED_MSG)
    via = "your phone" if channel == OtpChannel.SMS else "your email"
    return render(
        request,
        "registration/otp_verify.html",
        {
            "form": form,
            "heading": "Enter your recovery code",
            "intro": f"If an account matches, a 6-digit code was sent to {via}. It expires in 5 minutes.",
            "submit_label": "Verify code",
        },
    )
