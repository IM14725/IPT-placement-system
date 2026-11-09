"""Administrator account management (Super Admin only)."""

import secrets
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import AdminRole, User, UserRole, normalize_phone
from apps.accounts.sessions import revoke_user_session
from apps.core.audit import audit_event
from apps.core.providers import send_email


def generate_temp_password():
    return secrets.token_urlsafe(14)


def _deliver_credentials(user, temp_password, login_url):
    hours = settings.ADMIN_TEMP_PASSWORD_HOURS
    send_email(
        "Your IPT Marketplace administrator account",
        [user.email],
        f"An administrator account was created for you.\n\nSign in: {login_url}\n"
        f"Email: {user.email}\nTemporary password: {temp_password}\n\n"
        f"The temporary password expires in {hours} hours and must be replaced at first sign-in. "
        "You will then set up multi-factor authentication before getting access. "
        "Never share this email.",
    )


@transaction.atomic
def create_administrator(
    creator, *, first_name, last_name, email, phone="", admin_role=None, super_admin=False,
    job_title="", login_url="", request=None,
):
    """Create an admin with a generated temporary password delivered by email."""
    if not creator.is_super_admin:
        raise ValidationError("Only a Super Admin can create administrators.")
    email = (email or "").strip().lower()
    if User.objects.filter(email__iexact=email).exists():
        raise ValidationError(
            "This email address is already associated with an account. Please use another email address."
        )
    phone = normalize_phone(phone)
    if phone and User.objects.filter(phone=phone).exists():
        raise ValidationError(
            "This phone number is already associated with an account. Please use another phone number."
        )
    if not super_admin and admin_role is None:
        raise ValidationError("Choose an administrative role.")
    temp_password = generate_temp_password()
    user = User.objects.create_user(
        email=email,
        password=temp_password,
        role=UserRole.SUPER_ADMIN if super_admin else UserRole.ADMIN,
        admin_role=None if super_admin else admin_role,
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        phone=phone,
        job_title=job_title.strip(),
        must_change_password=True,
        temp_password_expires_at=timezone.now() + timedelta(hours=settings.ADMIN_TEMP_PASSWORD_HOURS),
    )
    audit_event(
        request, action="Admin Created", module="Administrators", actor=creator, target=user,
        target_label=user.email,
        description=f"Created {'Super Admin' if super_admin else 'administrator'} {user.email}",
        context={"role": "SUPER_ADMIN" if super_admin else admin_role.name, "job_title": user.job_title},
    )
    _deliver_credentials(user, temp_password, login_url)
    return user


def reissue_temp_password(creator, user, login_url="", request=None):
    """New temporary password for an admin who has not finished onboarding."""
    if not user.must_change_password:
        raise ValidationError("Only accounts that have not completed first sign-in can be reissued.")
    temp_password = generate_temp_password()
    user.set_password(temp_password)
    user.temp_password_expires_at = timezone.now() + timedelta(hours=settings.ADMIN_TEMP_PASSWORD_HOURS)
    user.save(update_fields=["password", "temp_password_expires_at"])
    revoke_user_session(user, "credentials_reissued")
    audit_event(
        request, action="Admin Temporary Password Reissued", module="Administrators", actor=creator,
        target=user, target_label=user.email, description="Issued a new temporary password",
    )
    _deliver_credentials(user, temp_password, login_url)


def _only_super_admin_guard(user):
    if user.is_super_admin and User.objects.filter(is_active=True, role=UserRole.SUPER_ADMIN).exclude(pk=user.pk).count() == 0:
        raise ValidationError("The last active Super Admin cannot be changed.")


@transaction.atomic
def change_admin_role(actor, user, admin_role, request=None):
    if user.role != UserRole.ADMIN:
        raise ValidationError("Only ordinary administrators have a role matrix.")
    old = user.admin_role.name if user.admin_role else ""
    user.admin_role = admin_role
    user.save(update_fields=["admin_role"])
    revoke_user_session(user, "role_changed")  # new privileges = new session id
    audit_event(
        request, action="Admin Role Changed", module="Administrators", actor=actor, target=user,
        target_label=user.email, description=f"{user.email}: {old or '-'} -> {admin_role.name}",
        context={"old": old, "new": admin_role.name},
    )


@transaction.atomic
def set_admin_suspended(actor, user, suspended, reason="", request=None):
    if user.pk == actor.pk:
        raise ValidationError("You cannot suspend your own account.")
    if suspended:
        _only_super_admin_guard(user)
    user.is_active = not suspended
    user.suspended_at = timezone.now() if suspended else None
    user.suspension_reason = reason if suspended else ""
    user.save(update_fields=["is_active", "suspended_at", "suspension_reason"])
    if suspended:
        revoke_user_session(user, "suspended")
    audit_event(
        request, action="Admin Suspended" if suspended else "Admin Reactivated", module="Administrators",
        actor=actor, target=user, target_label=user.email,
        description=f"{'Suspended' if suspended else 'Reactivated'} {user.email}" + (f": {reason}" if reason else ""),
    )


@transaction.atomic
def set_recovery_approver(actor, user, value, request=None):
    user.is_recovery_approver = bool(value)
    user.save(update_fields=["is_recovery_approver"])
    audit_event(
        request, action="Recovery Approver Designated" if value else "Recovery Approver Removed",
        module="Administrators", actor=actor, target=user, target_label=user.email,
        description=f"{user.email} {'is now' if value else 'is no longer'} a designated recovery approver",
    )
