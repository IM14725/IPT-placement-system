"""Account & security: change email/phone (verified by OTP on the NEW value) and password."""

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache

from apps.accounts import otp
from apps.accounts.forms import (
    ChangeEmailForm,
    ChangePasswordForm,
    ChangePhoneForm,
    OtpCodeForm,
)
from apps.accounts.models import OtpChannel, OtpPurpose, User, normalize_phone
from apps.accounts.sessions import sync_session_key
from apps.core.audit import audit_event
from apps.core.rate_limit import token_bucket

EMAIL_TAKEN = "This email address is already associated with an account. Please use another email address."
PHONE_TAKEN = "This phone number is already associated with an account. Please use another phone number."

_KINDS = {
    "email": (OtpPurpose.CHANGE_EMAIL, OtpChannel.EMAIL),
    "phone": (OtpPurpose.CHANGE_PHONE, OtpChannel.SMS),
}


def _user_ip_key(request):
    return f"user:{request.user.pk}"


def _org_of(user):
    from apps.accounts.sessions import _org_for

    return _org_for(user)


def _email_in_use(email, user):
    return User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists()


def _phone_in_use(phone, user):
    return bool(phone) and User.objects.filter(phone=phone).exclude(pk=user.pk).exists()


@login_required
@never_cache
def account_security(request):
    from apps.accounts.sessions import seconds_remaining

    return render(
        request,
        "registration/account_security.html",
        {
            "email_form": ChangeEmailForm(),
            "phone_form": ChangePhoneForm(initial={"phone": request.user.phone}),
            "session_seconds": seconds_remaining(request),
        },
    )


def _start_change(request, kind, form_class, template_forms):
    """Validate the new value, then send the OTP to *that* value. Nothing is saved yet."""
    user = request.user
    purpose, channel = _KINDS[kind]
    form = form_class(request.POST, user=user)
    if not form.is_valid():
        for errs in form.errors.values():
            for err in errs:
                messages.error(request, err)
        return redirect("account-security")
    new_value = form.cleaned_data[kind]
    if kind == "email":
        taken = _email_in_use(new_value, user)
        unchanged = new_value == user.email
    else:
        new_value = normalize_phone(new_value)
        taken = _phone_in_use(new_value, user)
        unchanged = new_value == user.phone and user.phone_verified
    if taken:
        messages.error(request, EMAIL_TAKEN if kind == "email" else PHONE_TAKEN)
        return redirect("account-security")
    if unchanged:
        messages.info(request, f"That is already your verified {kind}.")
        return redirect("account-security")
    result = otp.send_otp(user, purpose, channel, destination=new_value)
    if not result.sent:
        if result.retry_after:
            messages.error(request, f"Please wait {result.retry_after} seconds before requesting another code.")
        else:
            messages.error(request, "We could not send a code. Please check the details and try again.")
        return redirect("account-security")
    return redirect("verify-contact", kind=kind)


@login_required
@token_bucket(capacity=5, refill_per_second=1 / 60, scope="change-contact", key_fn=_user_ip_key, methods=["POST"])
def change_email(request):
    if request.method != "POST":
        return redirect("account-security")
    return _start_change(request, "email", ChangeEmailForm, None)


@login_required
@token_bucket(capacity=5, refill_per_second=1 / 60, scope="change-contact", key_fn=_user_ip_key, methods=["POST"])
def change_phone(request):
    if request.method != "POST":
        return redirect("account-security")
    return _start_change(request, "phone", ChangePhoneForm, None)


@login_required
@never_cache
@token_bucket(capacity=10, refill_per_second=1 / 30, scope="otp-verify", key_fn=_user_ip_key, methods=["POST"])
def verify_contact(request, kind):
    if kind not in _KINDS:
        return redirect("account-security")
    user = request.user
    purpose, channel = _KINDS[kind]
    destination = otp.pending_destination(user, purpose)
    if not destination:
        messages.error(request, "That change has expired. Please start again.")
        return redirect("account-security")
    form = OtpCodeForm(request.POST or None)
    if request.method == "POST":
        if request.POST.get("action") == "resend":
            result = otp.send_otp(user, purpose, channel, destination=destination)
            messages.success(request, "A new code has been sent.") if result.sent else messages.error(
                request,
                f"Please wait {result.retry_after} seconds before requesting another code."
                if result.retry_after
                else "We could not send a code. Please try again shortly.",
            )
            return redirect("verify-contact", kind=kind)
        if form.is_valid():
            result = otp.verify_otp(user, purpose, form.cleaned_data["code"])
            if result == otp.OK:
                return _apply_change(request, kind, destination)
            form.add_error(
                "code",
                "Incorrect code. The change was not saved."
                if result == otp.INVALID
                else "That code has expired. Request a new one.",
            )
    masked = destination if kind == "phone" else destination
    return render(
        request,
        "registration/otp_verify.html",
        {
            "form": form,
            "heading": f"Verify your new {kind}",
            "intro": f"We sent a 6-digit code to {masked}. Your {kind} is only changed once you enter it. The code expires in 5 minutes.",
            "submit_label": f"Confirm new {kind}",
        },
    )


def _apply_change(request, kind, value):
    user = request.user
    old = user.email if kind == "email" else user.phone
    try:
        with transaction.atomic():
            # Re-check at the moment of saving: someone may have claimed it since the code was sent.
            if kind == "email":
                if _email_in_use(value, user):
                    raise IntegrityError("email taken")
                user.email = value
                user.email_verified = True
                user.save(update_fields=["email", "email_verified"])
            else:
                if _phone_in_use(value, user):
                    raise IntegrityError("phone taken")
                user.phone = value
                user.phone_verified = True
                user.save(update_fields=["phone", "phone_verified"])
    except IntegrityError:
        messages.error(request, EMAIL_TAKEN if kind == "email" else PHONE_TAKEN)
        return redirect("account-security")
    audit_event(
        request,
        action=f"{kind.capitalize()} Changed",
        module="Security",
        organization=_org_of(user),
        target=user,
        target_label=user.email,
        description=f"Verified and saved a new {kind}",
        context={"old": old, "new": value},
    )
    messages.success(request, f"Your {kind} was verified and updated.")
    return redirect("account-security")


@login_required
@never_cache
@token_bucket(capacity=5, refill_per_second=1 / 60, scope="change-password", key_fn=_user_ip_key, methods=["POST"])
def change_password(request):
    """Also the forced first step for accounts created with a temporary password."""
    user = request.user
    forced = user.must_change_password
    form = ChangePasswordForm(request.POST or None, user=user)
    if request.method == "POST" and form.is_valid():
        user.set_password(form.cleaned_data["password1"])
        user.must_change_password = False
        user.temp_password_expires_at = None
        fields = ["password", "must_change_password", "temp_password_expires_at"]
        if forced and not user.email_verified:
            user.email_verified = True  # the temporary password was delivered to this address
            fields.append("email_verified")
        user.save(update_fields=fields)
        update_session_auth_hash(request, user)  # keeps this session, ends any other
        sync_session_key(request)  # new session id after the privilege-relevant change
        audit_event(
            request,
            action="Password Changed",
            module="Security",
            organization=_org_of(user),
            description="Replaced the temporary password" if forced else "Changed password",
        )
        messages.success(request, "Your password has been updated.")
        from apps.accounts.views import _dashboard_url

        return redirect(_dashboard_url(user))
    return render(
        request, "registration/change_password.html", {"form": form, "forced": forced}
    )
