from celery import shared_task
from django.db import IntegrityError, transaction
from apps.accounts.models import User
from apps.core.redis_client import acquire_lock, release_lock


@shared_task
def process_registration_task(form_data):
    """Create the account. ``form_data`` carries ``password_hash`` (never the raw password)."""
    email = form_data.get("email", "").strip().lower()

    # We acquire lock inside the task to prevent duplicate celery jobs
    token = acquire_lock(f"register:{email}", ttl_ms=10000, blocking=True)
    try:
        if User.objects.filter(email=email).exists():
            return {"status": "error", "message": "An account with this email already exists."}

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
                )
        else:
            user.first_name = (form_data.get("first_name") or "").strip()
            user.last_name = (form_data.get("last_name") or "").strip()
            user.save()
        return {"status": "success", "user_id": str(user.id)}
    except IntegrityError:
        return {
            "status": "error",
            "message": "An account with this email or phone number already exists.",
        }
    except Exception:
        return {"status": "error", "message": "Registration failed. Please try again."}
    finally:
        if token:
            release_lock(f"register:{email}", token)
