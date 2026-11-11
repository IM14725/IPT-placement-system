from celery import shared_task
from django.db import IntegrityError, transaction
from apps.accounts.models import User
from apps.core.providers import send_email
from apps.core.rate_limit import consume
from apps.core.redis_client import acquire_lock, release_lock

# Returned as ``user_id`` when the email already has an account. The browser sees the same
# success and the same "enter your code" page as a real signup, so nothing reveals the account.
DECOY_USER_ID = "decoy"
PHONE_UNAVAILABLE = "This phone number can't be used. Try another number or contact support."


def _notify_existing_owner(email, login_url="", reset_url=""):
    """Tell the real owner someone tried to register their address (at most once an hour)."""
    allowed = consume("register-notice", email, 1, 1 / 3600).allowed
    if not allowed:
        return
    lines = [
        "Someone just tried to create an IPT Marketplace account with this email address.",
        "",
        "You already have an account, so nothing was created and nothing has changed.",
    ]
    if login_url:
        lines += ["", f"Log in: {login_url}"]
    if reset_url:
        lines += [f"Forgot your password? Reset it here: {reset_url}"]
    lines += ["", "If this was not you, you can safely ignore this message."]
    try:
        send_email("Someone tried to register with your email", [email], "\n".join(lines))
    except Exception:  # noqa: BLE001 - a mail outage must not change what the visitor sees
        pass


@shared_task
def process_registration_task(form_data):
    """Create the account. ``form_data`` carries ``password_hash`` (never the raw password)."""
    email = form_data.get("email", "").strip().lower()

    # We acquire lock inside the task to prevent duplicate celery jobs
    token = acquire_lock(f"register:{email}", ttl_ms=10000, blocking=True)
    try:
        if User.objects.filter(email=email).exists():
            _notify_existing_owner(email, form_data.get("login_url", ""), form_data.get("reset_url", ""))
            return {"status": "success", "user_id": DECOY_USER_ID}

        user = User(
            email=email,
            role=form_data.get("role"),
            phone=form_data.get("phone", "")
        )
        user.password = form_data.get("password_hash") or ""
        if not user.password:
            return {"status": "error", "message": "Registration failed. Please try again."}

        if user.role == "ORGANIZATION":
            from apps.companies.services import organization_name_taken, create_organization

            user.first_name = (form_data.get("first_name") or "").strip()
            user.last_name = (form_data.get("last_name") or "").strip()
            organization_name = (form_data.get("organization_name") or "").strip()
            if not organization_name:
                return {"status": "error", "message": "Organization name is required."}
            if organization_name_taken(organization_name):
                return {"status": "error", "message": "An organization with this name is already registered."}
            with transaction.atomic():
                user.save()
                create_organization(
                    user,
                    organization_name,
                    email=form_data.get("organization_email") or "",
                    phone=form_data.get("organization_phone") or "",
                    position=form_data.get("position") or "",
                    brela_number=form_data.get("brela_number") or "",
                    tin=form_data.get("tin") or "",
                )
        else:
            user.first_name = (form_data.get("first_name") or "").strip()
            user.last_name = (form_data.get("last_name") or "").strip()
            with transaction.atomic():  # a unique-constraint clash must leave the connection usable
                user.save()
        from apps.core.funnel import track

        track("account_created")
        return {"status": "success", "user_id": str(user.id)}
    except IntegrityError:
        # A race on the unique constraints: an email clash is the decoy outcome, anything else is the phone.
        if User.objects.filter(email=email).exists():
            _notify_existing_owner(email, form_data.get("login_url", ""), form_data.get("reset_url", ""))
            return {"status": "success", "user_id": DECOY_USER_ID}
        return {"status": "error", "message": PHONE_UNAVAILABLE}
    except Exception:
        return {"status": "error", "message": "Registration failed. Please try again."}
    finally:
        if token:
            release_lock(f"register:{email}", token)
