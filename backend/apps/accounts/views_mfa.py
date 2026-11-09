"""Administrator MFA screens: challenge, setup, hardware step-up, recovery codes."""

import json
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.accounts import mfa
from apps.accounts.models import RecoveryCode, WebAuthnCredential
from apps.accounts.sessions import rotate_session_key
from apps.core import lockout
from apps.core.audit import audit_event
from apps.core.rate_limit import token_bucket

BACKEND = "django.contrib.auth.backends.ModelBackend"
MFA_LOCK_SCOPE = "mfa"


def _ip_key(request):
    return f"ip:{request.META.get('REMOTE_ADDR', 'unknown')}"


def _dashboard(user):
    from apps.accounts.views import _dashboard_url

    return _dashboard_url(user)


def _challenge_user(request):
    """The admin completing a second factor: pending sign-in, or an unverified live session."""
    pending = mfa.pending_user(request)
    if pending is not None:
        return pending, True
    if request.user.is_authenticated and mfa.is_admin_user(request.user):
        return request.user, False
    return None, False


def _safe_next(request, default):
    nxt = request.GET.get("next") or request.POST.get("next") or ""
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return nxt
    return default


def begin_mfa_challenge(request, user):
    """Password was right and the admin is enrolled: ask for a second factor before signing in."""
    import time

    request.session[mfa.MFA_PENDING] = str(user.id)
    request.session[mfa.MFA_PENDING_AT] = time.time()
    return redirect("mfa-challenge")


def _finish(request, user, pending, method, credential=None):
    """Second factor accepted: create (or upgrade) the session."""
    if pending:
        request.session.pop(mfa.MFA_PENDING, None)
        request.session.pop(mfa.MFA_PENDING_AT, None)
        login(request, user, backend=BACKEND)
    else:
        rotate_session_key(request)  # privilege elevation: new session id
    mfa.mark_mfa_ok(request, hardware=(method == "webauthn"))
    if method == "recovery":
        request.session[mfa.RECOVERY_USED] = True
    lockout.clear(MFA_LOCK_SCOPE, str(user.id))
    audit_event(
        request,
        action="Admin MFA Passed",
        module="Security",
        actor=user,
        description=f"Second factor accepted ({method})",
        context={"method": method},
    )
    if mfa.is_break_glass(user):
        from apps.accounts.breakglass import alert_break_glass_used

        alert_break_glass_used(request, user)
    return redirect(_safe_next(request, _dashboard(user)))


def _lock_message(remaining):
    minutes = max(1, -(-remaining // 60))
    return f"Too many failed attempts. Try again in {minutes} minute(s)."


@never_cache
@token_bucket(capacity=15, refill_per_second=1 / 20, scope="mfa-ip", key_fn=_ip_key, methods=["POST"])
def mfa_challenge(request):
    user, pending = _challenge_user(request)
    if user is None:
        return redirect("login")
    if not mfa.mfa_enrolled(user):
        return redirect("mfa-settings")
    methods = mfa.allowed_methods(user)
    error = None
    if request.method == "POST":
        remaining = lockout.lock_remaining(MFA_LOCK_SCOPE, str(user.id))
        if remaining:
            error = _lock_message(remaining)
        else:
            kind = request.POST.get("method")
            code = request.POST.get("code", "")
            if kind == "totp" and "totp" in methods and mfa.verify_totp(user, code):
                return _finish(request, user, pending, "totp")
            if kind == "recovery" and "recovery" in methods and mfa.consume_recovery_code(user, code):
                return _finish(request, user, pending, "recovery")
            lockout.register_failure(
                MFA_LOCK_SCOPE, str(user.id), settings.LOGIN_LOCKOUT_ATTEMPTS, settings.LOGIN_LOCKOUT_SECONDS
            )
            audit_event(
                request,
                action="Admin MFA Failed",
                module="Security",
                actor=user,
                outcome="FAILED",
                description="Second factor rejected",
                context={"method": kind or ""},
            )
            error = "That code was not accepted."
    return render(
        request,
        "registration/mfa_challenge.html",
        {"methods": methods, "error": error, "next": request.GET.get("next", ""), "email": user.email},
    )


# ── WebAuthn JSON endpoints ─────────────────────────────────────────────────

@require_POST
@never_cache
@token_bucket(capacity=15, refill_per_second=1 / 20, scope="mfa-ip", key_fn=_ip_key)
def webauthn_auth_options(request):
    user, _ = _challenge_user(request)
    if user is None or not mfa.has_fido(user):
        return JsonResponse({"error": "No security key is registered."}, status=400)
    if lockout.lock_remaining(MFA_LOCK_SCOPE, str(user.id)):
        return JsonResponse({"error": "Too many failed attempts."}, status=429)
    return JsonResponse(mfa.authentication_options(request, user))


@require_POST
@never_cache
@token_bucket(capacity=15, refill_per_second=1 / 20, scope="mfa-ip", key_fn=_ip_key)
def webauthn_auth_verify(request):
    """Used by both the sign-in challenge and the high-risk step-up."""
    user, pending = _challenge_user(request)
    if user is None:
        return JsonResponse({"error": "Not signed in."}, status=401)
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Bad request."}, status=400)
    cred = mfa.verify_assertion(request, user, payload)
    if cred is None:
        lockout.register_failure(
            MFA_LOCK_SCOPE, str(user.id), settings.LOGIN_LOCKOUT_ATTEMPTS, settings.LOGIN_LOCKOUT_SECONDS
        )
        audit_event(
            request, action="Admin MFA Failed", module="Security", actor=user, outcome="FAILED",
            description="Security key rejected", context={"method": "webauthn"},
        )
        return JsonResponse({"error": "The security key was not accepted."}, status=400)
    next_url = request.GET.get("next") or ""
    if pending or not request.session.get(mfa.MFA_OK):
        response = _finish(request, user, pending, "webauthn")
        target = response["Location"]
    else:
        # Hardware re-verification for a high-risk action.
        rotate_session_key(request)
        mfa.mark_mfa_ok(request, hardware=True)
        audit_event(
            request, action="Hardware Key Re-verified", module="Security", actor=user,
            description="Security key confirmed for a high-risk action",
        )
        target = _safe_next(request, _dashboard(user))
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        target = next_url
    return JsonResponse({"ok": True, "redirect": target})


@login_required
@never_cache
def mfa_step_up(request):
    """Re-verify with the hardware key before a high-risk administrative action."""
    if not mfa.is_admin_user(request.user):
        return redirect("home")
    return render(
        request,
        "registration/mfa_step_up.html",
        {
            "next": _safe_next(request, _dashboard(request.user)),
            "has_key": mfa.has_fido(request.user),
            "seconds": settings.HARDWARE_REVERIFY_SECONDS,
        },
    )


# ── Settings, TOTP and key enrollment ───────────────────────────────────────

@login_required
@never_cache
def mfa_settings(request):
    user = request.user
    if not mfa.is_admin_user(user):
        return redirect("account-security")
    return render(
        request,
        "registration/mfa_settings.html",
        {
            "has_totp": mfa.has_totp(user),
            "keys": user.webauthn_credentials.all(),
            "requires_fido": mfa.requires_fido(user),
            "enrolled": mfa.mfa_enrolled(user),
            "codes_left": mfa.remaining_recovery_codes(user),
            "codes_exist": RecoveryCode.objects.filter(user=user).exists(),
            "recovery_used": bool(request.session.get(mfa.RECOVERY_USED)),
            "break_glass": mfa.is_break_glass(user),
        },
    )


def _enrolled_now(request, method):
    """First factor enrolled in this session: it counts as having passed MFA."""
    if not request.session.get(mfa.MFA_OK):
        mfa.mark_mfa_ok(request, hardware=(method == "webauthn"))
        rotate_session_key(request)
    audit_event(
        request, action=f"MFA Enrolled ({method})", module="Security",
        description=f"Registered {'an authenticator app' if method == 'totp' else 'a security key'}",
    )


@login_required
@never_cache
def mfa_totp_setup(request):
    user = request.user
    if not mfa.is_admin_user(user):
        return redirect("account-security")
    if mfa.requires_fido(user):
        messages.error(request, "Super Admins must use a hardware security key; an authenticator app is not accepted.")
        return redirect("mfa-settings")
    secret = request.session.get("totp_setup_secret")
    if request.method == "POST":
        if secret and mfa.confirm_totp(user, secret, request.POST.get("code", "")):
            request.session.pop("totp_setup_secret", None)
            _enrolled_now(request, "totp")
            messages.success(request, "Authenticator app added.")
            return redirect("mfa-settings")
        messages.error(request, "That code did not match. Check the time on your phone and try again.")
    else:
        secret = request.session["totp_setup_secret"] = mfa.new_totp_secret()
    uri = mfa.provisioning_uri(user, secret)
    return render(
        request,
        "registration/mfa_totp_setup.html",
        {"secret": secret, "qr": mfa.qr_svg(uri)},
    )


@require_POST
@login_required
@never_cache
def webauthn_register_options(request):
    if not mfa.is_admin_user(request.user):
        return JsonResponse({"error": "Forbidden"}, status=403)
    return JsonResponse(mfa.registration_options(request, request.user))


@require_POST
@login_required
@never_cache
def webauthn_register_verify(request):
    if not mfa.is_admin_user(request.user):
        return JsonResponse({"error": "Forbidden"}, status=403)
    try:
        body = json.loads(request.body or b"{}")
        cred = mfa.register_credential(
            request, request.user, body.get("credential"), body.get("name", "")
        )
    except Exception:  # noqa: BLE001
        return JsonResponse({"error": "The security key could not be registered."}, status=400)
    _enrolled_now(request, "webauthn")
    return JsonResponse({"ok": True, "redirect": reverse("mfa-settings"), "name": cred.name})


@require_POST
@login_required
def mfa_recovery_codes(request):
    """Generate a fresh set of offline recovery codes, shown on this page exactly once."""
    user = request.user
    if not mfa.is_admin_user(user) or not mfa.mfa_enrolled(user):
        return redirect("mfa-settings")
    if not user.check_password(request.POST.get("password", "")):
        messages.error(request, "Your password is incorrect.")
        return redirect("mfa-settings")
    codes = mfa.generate_recovery_codes(user)
    request.session.pop(mfa.RECOVERY_USED, None)
    audit_event(
        request, action="Recovery Codes Generated", module="Security",
        description="Generated a new set of offline recovery codes (old codes invalidated)",
    )
    response = render(request, "registration/mfa_recovery_codes.html", {"codes": codes})
    response["Cache-Control"] = "no-store"
    return response


@require_POST
@login_required
def mfa_remove(request, kind, pk=None):
    user = request.user
    if not mfa.is_admin_user(user) or not user.check_password(request.POST.get("password", "")):
        messages.error(request, "Your password is incorrect.")
        return redirect("mfa-settings")
    if kind == "key":
        keys = user.webauthn_credentials.all()
        key = keys.filter(pk=pk).first()
        if key is None:
            return redirect("mfa-settings")
        if mfa.requires_fido(user) and keys.count() <= 1:
            messages.error(request, "A Super Admin must keep at least one security key. Add another before removing this one.")
            return redirect("mfa-settings")
        if not mfa.has_totp(user) and keys.count() <= 1:
            messages.error(request, "Add another factor before removing your only one.")
            return redirect("mfa-settings")
        key.delete()
        audit_event(request, action="Security Key Removed", module="Security", description=f"Removed {key.name}")
    elif kind == "totp":
        if mfa.requires_fido(user) or not mfa.has_fido(user):
            messages.error(request, "Add a security key before removing the authenticator app.")
            return redirect("mfa-settings")
        user.totp_device.delete()
        audit_event(request, action="Authenticator App Removed", module="Security", description="Removed the authenticator app")
    messages.success(request, "Removed.")
    return redirect("mfa-settings")


# ── self-service admin recovery request (M-of-N) ────────────────────────────

@token_bucket(capacity=3, refill_per_second=1 / 300, scope="admin-recovery", key_fn=_ip_key, methods=["POST"])
def admin_recovery_request(request):
    """An administrator locked out of MFA asks the designated approvers to reset it."""
    from apps.accounts import recovery

    if request.user.is_authenticated:
        return redirect(_dashboard(request.user))
    submitted = False
    if request.method == "POST":
        recovery.request_recovery((request.POST.get("email") or "").strip().lower(), request)
        submitted = True  # same answer whether or not anything was created
    return render(request, "registration/admin_recovery_request.html", {"submitted": submitted})
