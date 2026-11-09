import logging

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.signing import BadSignature, TimestampSigner
from django.utils.crypto import constant_time_compare
from django.contrib import messages
from django.db import IntegrityError
from django.http import JsonResponse, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache

from apps.accounts.forms import (
    ForgotPasswordForm,
    LoginForm,
    RegisterForm,
    ResetPasswordForm,
)
from django.conf import settings

from apps.accounts.models import User, UserRole
from apps.core import lockout
from apps.core.audit import client_ip
from apps.core.rate_limit import token_bucket
from apps.core.redis_client import acquire_lock, release_lock

logger = logging.getLogger("security")


# Post-registration auto-login handoff: the task id is bound to the browser session
# that submitted the form, and the signed finalize token is single-use and short-lived.
AUTH_TASK_SESSION_KEY = "auth_task_id"
AUTH_FINALIZE_SESSION_KEY = "auth_finalize_token"
AUTH_FINALIZE_SALT = "accounts.finalize-auth"
AUTH_FINALIZE_MAX_AGE = 60


def _register_ip_key(request):
    return f"ip:{client_ip(request) or 'unknown'}"


def _register_email_key(request):
    email = (request.POST.get("email") or "").strip().lower()
    return f"ip:{client_ip(request) or 'unknown'}:{email}"


def _register_denied(request, result):
    form = RegisterForm(request.POST or None)
    form.add_error(
        None,
        f"Too many registration attempts. Please try again in {max(1, int(result.retry_after))} seconds.",
    )
    response = render(request, "registration/register.html", {"form": form}, status=429)
    response["Retry-After"] = str(max(1, int(result.retry_after)))
    return response


def _login_rate_key(request):
    email = (request.POST.get("email") or "").strip().lower()
    return f"ip:{client_ip(request) or 'unknown'}:{email}"


def _login_ip_key(request):
    return f"ip:{client_ip(request) or 'unknown'}"


def _login_denied(request, result):
    form = LoginForm(request.POST or None)
    form.add_error(
        None,
        f"Too many login attempts. Please try again in {max(1, int(result.retry_after))} seconds.",
    )
    response = render(request, "registration/login.html", {"form": form}, status=429)
    response["Retry-After"] = str(max(1, int(result.retry_after)))
    return response


def home(request):
    return render(request, "home.html")


@token_bucket(
    capacity=10,
    refill_per_second=1 / 30,
    scope="register-ip",
    key_fn=_register_ip_key,
    deny_view=_register_denied,
    methods=["POST"],
    fail_closed=True,
)
@token_bucket(
    capacity=3,
    refill_per_second=1 / 60,
    scope="register-email",
    key_fn=_register_email_key,
    deny_view=_register_denied,
    methods=["POST"],
    fail_closed=True,
)
def register(request):
    if request.user.is_authenticated:
        return redirect(_dashboard_url(request.user))
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            from apps.accounts.tasks import process_registration_task

            # Only a salted hash crosses the Celery broker, never the raw password.
            form_data = {
                "email": form.cleaned_data.get("email"),
                "password_hash": make_password(form.cleaned_data.get("password1")),
                "role": str(form.cleaned_data.get("role")),
                "organization_name": form.cleaned_data.get("organization_name"),
                "organization_email": form.cleaned_data.get("organization_email"),
                "organization_phone": form.cleaned_data.get("organization_phone"),
                "position": form.cleaned_data.get("position"),
                "first_name": form.cleaned_data.get("first_name"),
                "last_name": form.cleaned_data.get("last_name"),
                "phone": form.cleaned_data.get("phone"),
            }
            
            task = process_registration_task.delay(form_data)
            request.session[AUTH_TASK_SESSION_KEY] = task.id
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                resp = HttpResponse(status=202)
                resp['X-Task-Id'] = task.id
                return resp
            return render(request, "registration/processing.html", {"task_id": task.id, "action": "register"})
    else:
        form = RegisterForm()
    return render(request, "registration/register.html", {"form": form})


@token_bucket(
    capacity=30,
    refill_per_second=1 / 30,
    scope="login-ip",
    key_fn=_login_ip_key,
    deny_view=_login_denied,
    methods=["POST"],
    fail_closed=True,
)
@token_bucket(
    capacity=5,
    refill_per_second=1 / 30,
    scope="login-account",
    key_fn=_login_rate_key,
    deny_view=_login_denied,
    methods=["POST"],
    fail_closed=True,
)
def user_login(request):
    if request.user.is_authenticated:
        return redirect(_dashboard_url(request.user))
    if request.method == "GET" and request.GET.get("reason") in ("expired", "elsewhere"):
        messages.info(
            request,
            "Your session has ended because it expired."
            if request.GET["reason"] == "expired"
            else "You were signed out because your account signed in on another device.",
        )
    if request.method == "POST":
        form = LoginForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"].strip().lower()
            remaining = lockout.lock_remaining("login", email)
            if remaining:
                minutes = max(1, -(-remaining // 60))
                form.add_error(
                    None,
                    f"Too many failed attempts. Please try again in {minutes} minute(s).",
                )
                response = render(request, "registration/login.html", {"form": form}, status=429)
                response["Retry-After"] = str(remaining)
                return response
            # Authenticated inline: the raw password must never be queued on the broker.
            user = authenticate(
                request, username=email, password=form.cleaned_data["password"]
            )
            if user is None:
                lockout.register_failure(
                    "login", email, settings.LOGIN_LOCKOUT_ATTEMPTS, settings.LOGIN_LOCKOUT_SECONDS
                )
                form.add_error(None, "Invalid email or password.")
            else:
                lockout.clear("login", email)
                response, error = _start_session(request, user)
                if response is not None:
                    return response
                form.add_error(None, error)
    else:
        form = LoginForm()
    return render(request, "registration/login.html", {"form": form})


def _needs_email_verification(user):
    return not user.email_verified and user.role in (UserRole.STUDENT, UserRole.ORGANIZATION)


def _start_session(request, user):
    """Start a session for an authenticated user.

    Students and organization members must have a verified email first: they are sent a
    code and routed to the verification page instead of being logged in. Otherwise the
    organization login cap is enforced and the session is created.
    Returns ``(redirect_response, None)`` or ``(None, error_message)``.
    """
    from django.utils import timezone

    from apps.companies.services import OrganizationSessionLimitError, can_organization_login

    if (
        user.must_change_password
        and user.temp_password_expires_at
        and user.temp_password_expires_at < timezone.now()
    ):
        return None, "Your temporary password has expired. Ask an administrator for a new one."
    if _needs_email_verification(user):
        from apps.accounts.views_otp import begin_email_verification

        return begin_email_verification(request, user), None
    from apps.accounts import mfa

    if (
        mfa.enforced()
        and mfa.is_admin_user(user)
        and not user.must_change_password
        and mfa.mfa_enrolled(user)
    ):
        # Enrolled administrators only get a session after a second factor.
        from apps.accounts.views_mfa import begin_mfa_challenge

        return begin_mfa_challenge(request, user), None
    try:
        ok, msg = can_organization_login(user, request.session.session_key)
        if ok:
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            return redirect(_dashboard_url(user)), None
    except OrganizationSessionLimitError as exc:
        msg = str(exc)
    return None, msg


@never_cache
def session_status(request):
    """Polled by the page so an expired/replaced session is noticed promptly in every tab."""
    from apps.accounts.sessions import seconds_remaining

    if not request.user.is_authenticated:
        return JsonResponse({"valid": False}, status=401)
    return JsonResponse({"valid": True, "expires_in": seconds_remaining(request)})


def user_logout(request):
    if getattr(request, "session", None) and request.session.session_key:
        from apps.companies.services import remove_organization_session

        remove_organization_session(request.session.session_key)
    logout(request)
    return redirect("home")


def dashboard_redirect(request):
    """Graceful handler for the legacy /dashboard/ path.

    Some cached pages/bookmarks from an earlier version point to /dashboard/.
    Instead of 404ing, route authenticated users to their role dashboard and
    anonymous users to login.
    """
    if not request.user.is_authenticated:
        return redirect("login")
    return redirect(_dashboard_url(request.user))


@login_required
@never_cache
def ws_ticket(request):
    """Issue a single-use, 60s ticket the realtime service redeems for a WebSocket."""
    from apps.core.ws_tickets import issue_ws_ticket

    return JsonResponse({"ticket": issue_ws_ticket(request.user), "user_id": str(request.user.id)})


@token_bucket(
    capacity=5,
    refill_per_second=1 / 60,
    scope="recovery-ip",
    key_fn=_login_ip_key,
    methods=["POST"],
    fail_closed=True,
)
def forgot_password(request):
    """Recovery step 1: enter the account email, then go on to choose how to get the code.

    Nothing is looked up here, so the response is identical whether or not the account exists.
    """
    from apps.accounts.views_otp import RESET_CHANNEL_KEY, RESET_EMAIL_KEY

    if request.user.is_authenticated:
        return redirect(_dashboard_url(request.user))
    if request.method == "POST":
        form = ForgotPasswordForm(request.POST)
        if form.is_valid():
            request.session[RESET_EMAIL_KEY] = form.cleaned_data["email"].strip().lower()
            request.session.pop(RESET_CHANNEL_KEY, None)
            return redirect("reset-method")
    else:
        form = ForgotPasswordForm()
    return render(request, "registration/forgot_password.html", {"form": form})


def reset_password(request):
    if request.user.is_authenticated:
        return redirect(_dashboard_url(request.user))
    # Set only by a successful OTP verification (views_otp.reset_verify).
    user_id = request.session.get("pwd_reset_verified_user_id")
    if not user_id:
        return redirect("forgot-password")
    user = User.objects.filter(id=user_id).first()
    if user is None:
        return redirect("forgot-password")
    if request.method == "POST":
        form = ResetPasswordForm(request.POST)
        if form.is_valid():
            try:
                validate_password(form.cleaned_data["password1"], user=user)
            except ValidationError as exc:
                form.add_error("password1", exc.messages)
                return render(
                    request,
                    "registration/reset_password.html",
                    {"form": form, "masked_email": _mask_email(user.email)},
                )
            user.set_password(form.cleaned_data["password1"])
            user.save(update_fields=["password"])
            from apps.accounts.sessions import revoke_user_session

            revoke_user_session(user, reason="password_reset")
            request.session.pop("pwd_reset_verified_user_id", None)
            messages.success(request, "Your password has been reset. Please log in.")
            return redirect("login")
    else:
        form = ResetPasswordForm()
    return render(
        request,
        "registration/reset_password.html",
        {"form": form, "masked_email": _mask_email(user.email)},
    )


def _mask_email(email):
    if "@" not in email:
        return email
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        return f"{local[0]}*@{domain}"
    return f"{local[0]}***{local[-1]}@{domain}"


def _dashboard_url(user):
    if user.is_student:
        return "student-dashboard"
    if user.is_organization:
        return "organization-dashboard"
    return "platform-verifications"


# ── Feature 1: Notification Opt-Out Settings ─────────────────────────────────

@login_required
def notification_settings(request):
    """Allow users to opt out of promotional SMS/email notifications (T&C §9.1)."""
    if request.method == "POST":
        # Checkbox is only sent when checked (opted IN); absence means opted OUT
        promo_opted_in = request.POST.get("promo_notifications") == "on"
        request.user.promo_sms_optout = not promo_opted_in
        request.user.save(update_fields=["promo_sms_optout"])
        messages.success(request, "Your notification preferences have been saved.")
        return redirect("notification-settings")
    return render(request, "registration/notification_settings.html")


# ── Feature 2: Account Deletion Request ──────────────────────────────────────

@login_required
def request_account_deletion(request):
    """Allow users to request account deletion as required by T&C §13."""
    from django.utils import timezone as tz
    if request.method == "POST":
        confirm_email = (request.POST.get("confirm_email") or "").strip().lower()
        if confirm_email == request.user.email:
            request.user.deletion_requested_at = tz.now()
            request.user.is_active = False
            request.user.save(update_fields=["deletion_requested_at", "is_active"])
            logout(request)
            messages.success(
                request,
                "Your account deletion request has been received. "
                "Your account is now deactivated and will be deleted in accordance with the PDPA.",
            )
            return redirect("home")
        else:
            messages.error(request, "The email address you entered does not match your account email.")
            return redirect("delete-account")
    return render(request, "registration/delete_account.html")
from celery.result import AsyncResult


@never_cache
@token_bucket(
    capacity=60,
    refill_per_second=2.0,
    scope="auth-task-status",
    key_fn=_login_ip_key,
)
def check_auth_task(request, task_id):
    # Only the browser session that started the task may collect its result.
    if not task_id or request.session.get(AUTH_TASK_SESSION_KEY) != task_id:
        return JsonResponse({'status': 'ERROR', 'message': 'Unknown request. Please try again.'})
    result = AsyncResult(task_id)
    if result.ready():
        try:
            res = result.get()
            if isinstance(res, dict) and res.get('status') == 'success':
                token = TimestampSigner(salt=AUTH_FINALIZE_SALT).sign(res['user_id'])
                request.session[AUTH_FINALIZE_SESSION_KEY] = token
                request.session.pop(AUTH_TASK_SESSION_KEY, None)
                return JsonResponse({'status': 'SUCCESS', 'token': token})
            else:
                error_msg = res.get('message', 'Registration could not be completed.') if isinstance(res, dict) else 'Registration could not be completed.'
                return JsonResponse({'status': 'ERROR', 'message': error_msg})
        except Exception:
            logger.exception("Error retrieving auth task result for task_id=%s", task_id)
            return JsonResponse({'status': 'ERROR', 'message': 'An unexpected error occurred while processing your request.'})
    return JsonResponse({'status': 'PENDING'})


@token_bucket(
    capacity=10,
    refill_per_second=1 / 6,
    scope="auth-finalize",
    key_fn=_login_ip_key,
    methods=["POST"],
    fail_closed=True,
)
def finalize_auth(request):
    """Start the session after registration. Token must be fresh, signed and single-use."""
    if request.method == 'POST':
        token = request.POST.get('token') or ''
        # Single-use: consumed whether or not it turns out to be valid.
        expected = request.session.pop(AUTH_FINALIZE_SESSION_KEY, None)
        try:
            if not expected or not constant_time_compare(token, expected):
                raise BadSignature("token not issued to this session")
            user_id = TimestampSigner(salt=AUTH_FINALIZE_SALT).unsign(
                token, max_age=AUTH_FINALIZE_MAX_AGE
            )
            user = User.objects.get(id=user_id, is_active=True)
        except (BadSignature, User.DoesNotExist):
            messages.error(request, 'Authentication failed. Please try again.')
            return redirect('login')
        response, error = _start_session(request, user)
        if response is not None:
            return response
        messages.error(request, error)
        return redirect('login')
    return redirect('home')

