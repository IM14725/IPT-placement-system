from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from rest_framework.authtoken.models import Token

from apps.accounts.forms import (
    ForgotPasswordForm,
    LoginForm,
    RegisterForm,
    ResetPasswordForm,
)
from apps.accounts.models import User
from apps.core.rate_limit import token_bucket
from apps.core.redis_client import acquire_lock, release_lock


def _register_ip_key(request):
    return f"ip:{request.META.get('REMOTE_ADDR', 'unknown')}"


def _register_email_key(request):
    email = (request.POST.get("email") or "").strip().lower()
    return f"ip:{request.META.get('REMOTE_ADDR', 'unknown')}:{email}"


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
    return f"ip:{request.META.get('REMOTE_ADDR', 'unknown')}:{email}"


def _login_ip_key(request):
    return f"ip:{request.META.get('REMOTE_ADDR', 'unknown')}"


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
)
@token_bucket(
    capacity=3,
    refill_per_second=1 / 60,
    scope="register-email",
    key_fn=_register_email_key,
    deny_view=_register_denied,
    methods=["POST"],
)
def register(request):
    if request.user.is_authenticated:
        return redirect(_dashboard_url(request.user))
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            from apps.accounts.tasks import process_registration_task
            
            # Make sure form_data is json serializable
            form_data = {
                "email": form.cleaned_data.get("email"),
                "password1": form.cleaned_data.get("password1"),
                "role": str(form.cleaned_data.get("role")),
                "company_name": form.cleaned_data.get("company_name"),
                "first_name": form.cleaned_data.get("first_name"),
                "last_name": form.cleaned_data.get("last_name"),
            }
            
            task = process_registration_task.delay(form_data)
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
)
@token_bucket(
    capacity=5,
    refill_per_second=1 / 30,
    scope="login-account",
    key_fn=_login_rate_key,
    deny_view=_login_denied,
    methods=["POST"],
)
def user_login(request):
    if request.user.is_authenticated:
        return redirect(_dashboard_url(request.user))
    if request.method == "POST":
        form = LoginForm(request.POST)
        if form.is_valid():
            from apps.accounts.tasks import process_login_task
            email = form.cleaned_data["email"]
            password = form.cleaned_data["password"]
            task = process_login_task.delay(email, password)
            return render(request, "registration/processing.html", {"task_id": task.id, "action": "login"})
    else:
        form = LoginForm()
    return render(request, "registration/login.html", {"form": form})


def user_logout(request):
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


from django.views.decorators.cache import never_cache

@login_required
@never_cache
def my_token(request):
    token, _ = Token.objects.get_or_create(user=request.user)
    return JsonResponse({"token": token.key, "user_id": request.user.id})


def forgot_password(request):
    if request.user.is_authenticated:
        return redirect(_dashboard_url(request.user))
    if request.method == "POST":
        form = ForgotPasswordForm(request.POST)
        if form.is_valid():
            user = User.objects.get(email=form.cleaned_data["email"])
            request.session["pwd_reset_user_id"] = user.id
            request.session["pwd_reset_phone"] = form.cleaned_data["phone"]
            return redirect("reset-password")
    else:
        form = ForgotPasswordForm()
    return render(request, "registration/forgot_password.html", {"form": form})


def reset_password(request):
    if request.user.is_authenticated:
        return redirect(_dashboard_url(request.user))
    user_id = request.session.get("pwd_reset_user_id")
    if not user_id:
        return redirect("forgot-password")
    user = User.objects.filter(id=user_id).first()
    if user is None:
        return redirect("forgot-password")
    if request.method == "POST":
        form = ResetPasswordForm(request.POST)
        if form.is_valid():
            user.set_password(form.cleaned_data["password1"])
            user.save(update_fields=["password"])
            del request.session["pwd_reset_user_id"]
            del request.session["pwd_reset_phone"]
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
    if user.is_company:
        return "company-dashboard"
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
from django.core.signing import Signer
import json

def check_auth_task(request, task_id):
    result = AsyncResult(task_id)
    if result.ready():
        res = result.get()
        if res.get('status') == 'success':
            signer = Signer()
            token = signer.sign(res['user_id'])
            return JsonResponse({'status': 'SUCCESS', 'token': token})
        else:
            return JsonResponse({'status': 'ERROR', 'message': res.get('message', 'Failed')})
    return JsonResponse({'status': 'PENDING'})

def finalize_auth(request):
    if request.method == 'POST':
        token = request.POST.get('token')
        signer = Signer()
        try:
            user_id = signer.unsign(token)
            user = User.objects.get(id=user_id)
            login(request, user)
            return redirect(_dashboard_url(user))
        except Exception:
            messages.error(request, 'Authentication failed. Please try again.')
            return redirect('login')
    return redirect('home')

