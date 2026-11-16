"""Platform console: every one-time code users have requested, how it was delivered and what became of it.

Read access needs the ``user_management`` module (view); revoke / resend / reveal need edit and, like
other high-risk admin POSTs, a fresh hardware key. The plain code is only ever shown for a code that a
mock adapter delivered (dev), never for a real SMS or email.
"""

from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.accounts import otp as otp_lib
from apps.accounts.decorators import hardware_reverify
from apps.accounts.models import OneTimeCode, OtpChannel, OtpDelivery, OtpOutcome, OtpPurpose
from apps.core.audit import audit_event
from apps.core.pagination import paginate

STATUS_CHOICES = [
    (OtpOutcome.PENDING, "Pending"),
    (OtpOutcome.VERIFIED, "Verified"),
    (OtpOutcome.EXPIRED, "Expired"),
    (OtpOutcome.LOCKED, "Locked out (too many wrong guesses)"),
    (OtpOutcome.SUPERSEDED, "Replaced by a newer code"),
    (OtpOutcome.REVOKED, "Revoked"),
    (OtpOutcome.UNDELIVERED, "Not delivered"),
]


def _gate(request):
    from apps.core.views import _require_admin

    return _require_admin(request, module="user_management")


def filter_requests(qs, params):
    now = timezone.now()
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(user__email__icontains=q)
            | Q(user__first_name__icontains=q)
            | Q(user__last_name__icontains=q)
            | Q(user__phone__icontains=q)
            | Q(destination__icontains=q)
        )
    if params.get("channel") in OtpChannel.values:
        qs = qs.filter(channel=params["channel"])
    if params.get("purpose") in OtpPurpose.values:
        qs = qs.filter(purpose=params["purpose"])
    if params.get("delivery") in (OtpDelivery.SENT, OtpDelivery.FAILED):
        qs = qs.filter(delivery_status=params["delivery"])
    status = params.get("status")
    if status == OtpOutcome.PENDING:
        qs = qs.filter(outcome=OtpOutcome.PENDING, consumed_at__isnull=True, expires_at__gt=now)
    elif status == OtpOutcome.EXPIRED:
        qs = qs.filter(outcome=OtpOutcome.PENDING, consumed_at__isnull=True, expires_at__lte=now)
    elif status == OtpOutcome.SUPERSEDED:  # includes rows closed before outcomes were recorded
        qs = qs.filter(
            Q(outcome=OtpOutcome.SUPERSEDED) | Q(outcome=OtpOutcome.PENDING, consumed_at__isnull=False)
        )
    elif status in (OtpOutcome.VERIFIED, OtpOutcome.LOCKED, OtpOutcome.REVOKED, OtpOutcome.UNDELIVERED):
        qs = qs.filter(outcome=status)
    date_from, date_to = parse_date(params.get("date_from") or ""), parse_date(params.get("date_to") or "")
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    return qs


@login_required
def otp_requests(request):
    if not _gate(request):
        return render(request, "core/forbidden.html", status=403)
    params = request.GET
    now = timezone.now()
    # A stored dev code never outlives its code.
    OneTimeCode.objects.filter(expires_at__lte=now).exclude(debug_code="").update(debug_code="")
    qs = filter_requests(OneTimeCode.objects.select_related("user"), params).order_by("-created_at")
    page_obj = paginate(qs, params.get("page"), page_size=50)
    total = OneTimeCode.objects.count()
    for row in page_obj:
        row.resend_ok = can_resend(row)  # the Resend button follows the same rule as the action
    return render(
        request,
        "core/otp_requests.html",
        {
            "rows": page_obj,
            "page_obj": page_obj,
            "querystring": urlencode({k: v for k, v in params.items() if k != "page"}),
            "f": params,
            "total": total,
            "channels": OtpChannel.choices,
            "purposes": OtpPurpose.choices,
            "statuses": STATUS_CHOICES,
            "can_edit": _can_edit(request),
            "codes_visible": bool(getattr(settings, "OTP_STORE_MOCK_CODE", False)),
            "sms_backend": getattr(settings, "SMS_BACKEND", "auto"),
        },
    )


def can_resend(row):
    """Same rule as the user-facing flows: a code only goes to a contact method that is on the account now
    (and, for password recovery, one that is verified), never to an address from an old request."""
    if row.outcome == OtpOutcome.VERIFIED or not row.user.is_active:
        return False
    if row.purpose in (OtpPurpose.RECOVERY, OtpPurpose.REGISTER):
        if row.destination != otp_lib.destination_for(row.user, row.channel):
            return False
    if row.purpose == OtpPurpose.RECOVERY:
        return otp_lib.channel_verified(row.user, row.channel)
    return True


def _can_edit(request):
    user = request.user
    return user.is_super_admin or (user.is_platform_admin and user.has_admin_permission("user_management", "edit"))


@login_required
@hardware_reverify
def otp_action(request, pk):
    """revoke | resend | reveal one code. Every one is audited."""
    if request.method != "POST":  # e.g. coming back from the hardware-key step-up, which returns by GET
        return redirect("platform-otp-requests")
    if not _gate(request):  # a POST needs the edit capability
        return render(request, "core/forbidden.html", status=403)
    row = get_object_or_404(OneTimeCode.objects.select_related("user"), pk=pk)
    action = request.POST.get("action")
    qs = request.POST.get("qs", "")
    back = redirect(reverse("platform-otp-requests") + (f"?{qs}" if qs else ""))
    ctx = {
        "purpose": row.purpose,
        "channel": row.channel,
        "destination": row.destination,
        "code_id": str(row.pk),
    }
    if action == "revoke":
        if otp_lib.revoke(row):
            audit_event(
                request, action="OTP Revoked", module="Security", target=row.user,
                target_label=row.user.email, description=f"Revoked a pending {row.get_purpose_display()} code",
                context=ctx,
            )
            messages.success(request, "The code was revoked. It can no longer be used.")
        else:
            messages.info(request, "That code was already used, expired or replaced.")
        return back
    if action == "resend":
        if not can_resend(row):
            messages.error(request, "A new code cannot be issued for this request.")
            return back
        result = otp_lib.send_otp(row.user, row.purpose, row.channel, destination=row.destination)
        audit_event(
            request, action="OTP Resent", module="Security", target=row.user, target_label=row.user.email,
            description=f"Issued a new {row.get_purpose_display()} code to {row.destination}",
            outcome="SUCCESS" if result.sent else "FAILED", context=ctx,
        )
        if result.sent:
            messages.success(request, f"A new code was sent to {row.destination}. The earlier one no longer works.")
        elif result.retry_after:
            messages.error(request, f"Too many codes for this user. Try again in {result.retry_after} seconds.")
        else:
            messages.error(request, "The code could not be delivered. See the delivery error on the new row.")
        return back
    if action == "reveal":
        if not (getattr(settings, "OTP_STORE_MOCK_CODE", False) and row.debug_code and row.is_live):
            messages.error(request, "No code is available for this request. Real SMS and email codes are never stored.")
            return back
        audit_event(
            request, action="OTP Code Revealed", module="Security", target=row.user,
            target_label=row.user.email, description="Viewed a mock-delivered one-time code (dev)",
            context=ctx,
        )
        return render(request, "core/otp_reveal.html", {"row": row, "back_qs": qs})
    messages.error(request, "Unknown action.")
    return back
