"""Break-glass emergency Super Admin accounts.

Credentials are high entropy, shown once for offline storage in a tamper-evident safe, and
the account is protected by a hardware key. It follows every normal control: lockout, MFA,
the 10-minute session, and full audit logging (its actions are tagged ``break_glass``).
Any use raises an immediate alert and flags the account for security review and credential
rotation.
"""

import secrets

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import BreakGlassAccount, User, UserRole
from apps.core.audit import audit_event
from apps.core.providers import send_email


def _new_password():
    return secrets.token_urlsafe(32)  # ~256 bits


@transaction.atomic
def create_break_glass(creator, email, request=None):
    """Returns ``(account, plain_password)``. The password is not recoverable afterwards."""
    email = (email or "").strip().lower()
    if User.objects.filter(email__iexact=email).exists():
        raise ValueError("This email address is already associated with an account. Please use another email address.")
    password = _new_password()
    user = User.objects.create_user(
        email=email,
        password=password,
        role=UserRole.SUPER_ADMIN,
        first_name="Break-glass",
        last_name="Emergency",
        email_verified=True,
    )
    account = BreakGlassAccount.objects.create(user=user, created_by=creator)
    audit_event(
        request, action="Break-glass Account Created", module="Security", actor=creator, target=user,
        target_label=user.email,
        description=f"Created emergency account {user.email}; register its hardware key and seal the credentials offline",
    )
    return account, password


@transaction.atomic
def rotate_break_glass(account, by, request=None):
    from apps.accounts.sessions import revoke_user_session

    password = _new_password()
    account.user.set_password(password)
    account.user.save(update_fields=["password"])
    account.rotation_required = False
    account.save(update_fields=["rotation_required"])
    revoke_user_session(account.user, "rotated")
    audit_event(
        request, action="Break-glass Credentials Rotated", module="Security", actor=by,
        target=account.user, target_label=account.user.email,
        description="Rotated the emergency password; store the new credential offline",
    )
    return password


def mark_reviewed(account, by, request=None):
    account.review_pending = False
    account.save(update_fields=["review_pending"])
    audit_event(
        request, action="Break-glass Review Completed", module="Security", actor=by,
        target=account.user, target_label=account.user.email,
        description="Security review of emergency access completed",
    )


def alert_break_glass_used(request, user):
    """Immediate, high-priority alert; the account is flagged for review and rotation."""
    BreakGlassAccount.objects.filter(user=user).update(
        last_used_at=timezone.now(), rotation_required=True, review_pending=True
    )
    audit_event(
        request, action="Break-glass Account Used", module="Security", actor=user, outcome="SUCCESS",
        description=f"EMERGENCY ACCESS: {user.email} signed in",
        context={"priority": "critical", "break_glass": True},
    )
    recipients = list(
        User.objects.filter(is_active=True, role=UserRole.SUPER_ADMIN)
        .exclude(pk=user.pk)
        .values_list("email", flat=True)
    )
    for to in recipients:
        try:
            send_email(
                "[URGENT] Break-glass emergency account was used",
                [to],
                f"The emergency account {user.email} just signed in from "
                f"{request.META.get('REMOTE_ADDR', 'unknown')}. If this was not an agreed emergency, "
                "treat it as a security incident. Review the audit log, then rotate its credentials.",
            )
        except Exception:  # noqa: BLE001
            pass
