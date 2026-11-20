"""Abandoned sign-ups.

A student or organization that registers but never enters the emailed code has an account nobody has proven
they own: it has never signed in, nothing was verified, and nothing can be attached to it. Such an
"unclaimed" account must not trap the person who started it:

* registering again with the same email resumes it (a new code is sent) instead of showing a dead end;
* its phone number, organization name and organization email do not block a new sign-up (a typo in the
  first attempt would otherwise make the right details look "already taken"): the unclaimed account holding
  them is released;
* email recovery works for it ("Forgot password" proves the mailbox, which verifies the email).

Accounts invited by an organization or created by an administrator carry a temporary password
(``must_change_password``) and are never treated as unclaimed.
"""
from __future__ import annotations

import logging

from django.db.models import Q

from apps.accounts.models import User, UserRole, normalize_phone

log = logging.getLogger("security")


def unclaimed_users():
    return User.objects.filter(
        role__in=[UserRole.STUDENT, UserRole.ORGANIZATION],
        is_active=True,
        email_verified=False,
        phone_verified=False,
        must_change_password=False,
        last_login__isnull=True,
    )


def is_unclaimed(user) -> bool:
    return user is not None and unclaimed_users().filter(pk=user.pk).exists()


def release_conflicts(*, keep_email: str = "", phone: str = "", organization_name: str = "", organization_email: str = "") -> int:
    """Delete unclaimed accounts (other than ``keep_email``) that hold the phone, organization name or
    organization email of a new sign-up. Returns how many were removed."""
    conditions = Q()
    phone = normalize_phone(phone)
    if phone:
        conditions |= Q(phone=phone)
    if (organization_name or "").strip():
        conditions |= Q(owned_organization__name__iexact=organization_name.strip())
    if (organization_email or "").strip():
        conditions |= Q(owned_organization__email__iexact=organization_email.strip())
    if not conditions:
        return 0
    removed = 0
    for user in unclaimed_users().exclude(email=keep_email).filter(conditions):
        try:
            user.delete()
            removed += 1
        except Exception:  # noqa: BLE001 - something is attached after all: leave it, the sign-up gets the generic message
            log.warning("Could not release unclaimed account %s", user.pk, exc_info=True)
    return removed
