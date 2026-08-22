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
            email = form.cleaned_data.get("email", "").strip().lower()
            token = acquire_lock(f"register:{email}", ttl_ms=5000, blocking=True)
            try:
                if not form.is_valid():
                    return render(request, "registration/register.html", {"form": form})
                user = form.save()
            except IntegrityError:
                form.add_error("email", "An account with this email already exists.")
            else:
                login(request, user)
                return redirect(_dashboard_url(user))
            finally:
                if token:
                    release_lock(f"register:{email}", token)
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
            user = authenticate(
                request,
                username=form.cleaned_data["email"],
                password=form.cleaned_data["password"],
            )
            if user is not None:
                login(request, user)
                return redirect(_dashboard_url(user))
            form.add_error(None, "Invalid email or password.")
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


@login_required
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