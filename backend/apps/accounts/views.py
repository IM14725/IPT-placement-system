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
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.tasks import DECOY_USER_ID
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
DECOY_EMAIL_SESSION_KEY = "register_typed_email"
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
    response = render(
        request,
        "registration/register.html",
        {"form": form, "accept_terms": bool(request.POST.get("accept_terms"))},
        status=429,
    )
    response["Retry-After"] = str(max(1, int(result.retry_after)))
    return response


def _login_rate_key(request):
    email = (request.POST.get("email") or "").strip().lower()
    return f"ip:{client_ip(request) or 'unknown'}:{email}"


def _login_ip_key(request):
    return f"ip:{client_ip(request) or 'unknown'}"


def _login_blocked(request, form, seconds, message):
    """429 login page with a live countdown to when the user may try again."""
    seconds = max(1, int(seconds))
    response = render(
        request,
        "registration/login.html",
        {
            "form": form,
            "lockout_seconds": seconds,
            "lockout_clock": f"{seconds // 60}:{seconds % 60:02d}",
            "lockout_message": message,
        },
        status=429,
    )
    response["Retry-After"] = str(seconds)
    return response


def _login_denied(request, result):
    # The rate limiter runs before the lockout check; never show a shorter wait than the lock.
    email = (request.POST.get("email") or "").strip().lower()
    locked_for = lockout.lock_remaining("login", email) if email else 0
    if locked_for > result.retry_after:
        seconds, message = locked_for, "Too many failed attempts."
    else:
        seconds, message = result.retry_after, "Too many login attempts."
    return _login_blocked(request, LoginForm(request.POST or None), seconds, message)


def _search_filters(request):
    return {name: request.GET.get(name, "") for name in ("region", "district", "industry", "level")}


def _count_region_search(request, filters):
    """Remember which regions people look for (a per-day counter, no identifiers) so the welcome page can lead with them."""
    from apps.accounts.public import record_region_search
    from apps.core.funnel import is_bot

    if (filters["region"] or filters["district"]) and not is_bot(request):
        record_region_search(filters["region"], filters["district"])


def home(request):
    from apps.accounts.public import get_public_home
    from apps.core.cache import get_districts, get_regions
    from apps.core.education import EducationLevel
    from apps.core.funnel import track

    if request.method == "GET" and not request.user.is_authenticated:
        track("home", request)
    context = get_public_home(**_search_filters(request))
    _count_region_search(request, context["filters"])
    selected_region = context["filters"]["region"]
    context.update(
        {
            "regions": get_regions(),
            # Server-rendered so the district list also works without JavaScript.
            "districts": get_districts(selected_region) if selected_region else [],
            "levels": EducationLevel.choices,
        }
    )
    return render(request, "home.html", context)


# ?role=student / ?role=organization on /auth/register/ (the welcome page's two entry buttons)
ROLE_PARAMS = {"student": UserRole.STUDENT, "organization": UserRole.ORGANIZATION}

FUNNEL_BEACON_BUCKET = dict(capacity=20, refill_per_second=1 / 6, scope="funnel-beacon")


def _funnel_ip_key(request):
    return f"ip:{request.META.get('REMOTE_ADDR', 'unknown')}"


@token_bucket(key_fn=_funnel_ip_key, methods=["POST"], **FUNNEL_BEACON_BUCKET)
@require_POST
@csrf_exempt  # a bare counter bump from the wizard; whitelisted step names, rate limited, carries no data
def funnel_event(request):
    from apps.core.funnel import BROWSER_STEPS, track

    step = request.POST.get("step", "")
    if step not in BROWSER_STEPS:
        return HttpResponse(status=400)
    track(step, request)
    return HttpResponse(status=204)


@token_bucket(capacity=30, refill_per_second=1 / 2, scope="public-slots", key_fn=_funnel_ip_key, methods=["GET"])
@require_GET
def public_slots_fragment(request):
    """HTML for the welcome page's slot cards, used to refresh them in place while searching."""
    from apps.accounts.public import get_public_home

    context = get_public_home(**_search_filters(request))
    _count_region_search(request, context["filters"])
    response = render(request, "includes/public_slots.html", context)
    response["X-Robots-Tag"] = "noindex"
    return response


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
            from apps.core.funnel import track

            track("register_submit", request)

            # Only a salted hash crosses the Celery broker, never the raw password.
            form_data = {
                "email": form.cleaned_data.get("email"),
                "password_hash": make_password(form.cleaned_data.get("password1")),
                "role": str(form.cleaned_data.get("role")),
                "organization_name": form.cleaned_data.get("organization_name"),
                "organization_email": form.cleaned_data.get("organization_email"),
                "organization_phone": form.cleaned_data.get("organization_phone"),
                "position": form.cleaned_data.get("position"),
                "brela_number": form.cleaned_data.get("brela_number"),
                "tin": form.cleaned_data.get("tin"),
                "first_name": form.cleaned_data.get("first_name"),
                "last_name": form.cleaned_data.get("last_name"),
                "phone": form.cleaned_data.get("phone"),
                "login_url": request.build_absolute_uri(reverse("login")),
                "reset_url": request.build_absolute_uri(reverse("forgot-password")),
            }
            request.session[DECOY_EMAIL_SESSION_KEY] = form_data["email"]

            task = process_registration_task.delay(form_data)
            request.session[AUTH_TASK_SESSION_KEY] = task.id
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                resp = HttpResponse(status=202)
                resp['X-Task-Id'] = task.id
                return resp
            return render(request, "registration/processing.html", {"task_id": task.id, "action": "register"})
    else:
        from apps.core.funnel import track

        track("register_view", request)
        role = ROLE_PARAMS.get((request.GET.get("role") or "").strip().lower())
        form = RegisterForm(initial={"role": role} if role else None)
        preselected = bool(role)
    return render(
        request,
        "registration/register.html",
        {
            "form": form,
            "accept_terms": bool(request.POST.get("accept_terms")),
            "preselected_role": request.method != "POST" and preselected,
        },
    )


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
                return _login_blocked(request, form, remaining, "Too many failed attempts.")
            # Authenticated inline: the raw password must never be queued on the broker.
            user = authenticate(
                request, username=email, password=form.cleaned_data["password"]
            )
            if user is None:
                locked = lockout.register_failure(
                    "login", email, settings.LOGIN_LOCKOUT_ATTEMPTS, settings.LOGIN_LOCKOUT_SECONDS
                )
                if locked:
                    # This was the failure that tripped the lock: start the countdown now.
                    return _login_blocked(
                        request,
                        form,
                        lockout.lock_remaining("login", email) or settings.LOGIN_LOCKOUT_SECONDS,
                        "Too many failed attempts.",
                    )
                form.add_error(None, "Invalid email or password.")
            else:
                lockout.clear("login", email)
                _cancel_pending_recovery(request, user)
                response, error = _start_session(request, user)
                if response is not None:
                    return response
                form.add_error(None, error)
    else:
        form = LoginForm()
    return render(request, "registration/login.html", {"form": form})


def _cancel_pending_recovery(request, user):
    """The user remembered their password: a reset code requested earlier must stop working."""
    from apps.accounts import otp
    from apps.accounts.models import OtpPurpose
    from apps.accounts.views_otp import RESET_CHANNEL_KEY, RESET_EMAIL_KEY

    otp.invalidate_pending(user, OtpPurpose.RECOVERY)
    for key in (RESET_EMAIL_KEY, RESET_CHANNEL_KEY, "pwd_reset_verified_user_id"):
        request.session.pop(key, None)


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


@require_POST
@never_cache
def session_extend(request):
    """The page saw real activity (movement, keys, touch): restart the 10-minute idle window."""
    from apps.accounts.sessions import seconds_remaining, touch

    if not request.user.is_authenticated:
        return JsonResponse({"valid": False}, status=401)
    touch(request)
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
            if user_id == DECOY_USER_ID:
                # The address already has an account: show the same code page, but no code will work.
                from apps.accounts.views_otp import begin_decoy_verification

                return begin_decoy_verification(request)
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

