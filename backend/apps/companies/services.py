from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User, UserRole
from apps.companies.models import (
    MAX_ORGANIZATION_ACCOUNTS,
    MAX_ORGANIZATION_CONCURRENT_LOGINS,
    Organization,
    MembershipStatus,
    Organization,
    OrganizationMember,
    OrganizationMemberRole,
    OrganizationSession,
    OrganizationType,
)


class OrganizationSessionLimitError(Exception):
    pass


def organization_name_taken(name):
    return Organization.objects.filter(name__iexact=(name or "").strip()).exists()


organization_name_taken = organization_name_taken


def create_organization(user, name, organization_type=OrganizationType.COMPANY):
    """Create the organization record and owner membership at registration."""
    org = Organization.objects.create(
        user=user,
        name=(name or "").strip(),
        organization_type=organization_type,
        email=getattr(user, "email", "") or "",
        phone=getattr(user, "phone", "") or "",
        industry="",
    )
    OrganizationMember.objects.get_or_create(
        organization=org,
        user=user,
        defaults={
            "role": OrganizationMemberRole.OWNER,
            "membership_status": MembershipStatus.ACTIVE,
        },
    )
    return org


create_organization = create_organization


def get_user_organization(user):
    if not user or not getattr(user, "is_authenticated", False):
        return None
    org = Organization.objects.filter(user=user).first()
    if org is not None:
        return org
    membership = (
        OrganizationMember.objects.filter(
            user=user, membership_status=MembershipStatus.ACTIVE
        )
        .select_related("organization")
        .first()
    )
    return membership.organization if membership else None


def is_profile_complete(organization):
    return bool(organization and organization.industry)


@transaction.atomic
def add_organization_member(
    organization, email, password, role=OrganizationMemberRole.RECRUITER, phone=""
):
    """Create or attach a staff member account to an organization (max 5 active accounts)."""
    locked_org = Organization.objects.select_for_update().get(pk=organization.pk)
    active_count = OrganizationMember.objects.filter(
        organization=locked_org, membership_status=MembershipStatus.ACTIVE
    ).count()
    if active_count >= MAX_ORGANIZATION_ACCOUNTS:
        raise ValidationError(
            f"An organization can have at most {MAX_ORGANIZATION_ACCOUNTS} active accounts."
        )
    email_norm = (email or "").strip().lower()
    if not email_norm:
        raise ValidationError("Email address is required.")
    if User.objects.filter(email=email_norm).exists():
        raise ValidationError("An account with this email already exists.")
    user = User.objects.create_user(
        email=email_norm,
        password=password,
        role=UserRole.ORGANIZATION,
        phone=phone or "",
        first_name="",
        last_name="",
    )
    member = OrganizationMember.objects.create(
        organization=locked_org,
        user=user,
        role=role,
        membership_status=MembershipStatus.ACTIVE,
    )
    return member


def cleanup_expired_organization_sessions(organization):
    """Remove OrganizationSession rows whose underlying Django session has expired or been deleted."""
    org_sessions = list(
        OrganizationSession.objects.filter(organization=organization).values_list(
            "session_key", flat=True
        )
    )
    if not org_sessions:
        return
    valid_keys = set(
        Session.objects.filter(
            session_key__in=org_sessions,
            expire_date__gt=timezone.now(),
        ).values_list("session_key", flat=True)
    )
    stale_keys = [k for k in org_sessions if k not in valid_keys]
    if stale_keys:
        OrganizationSession.objects.filter(
            organization=organization, session_key__in=stale_keys
        ).delete()


def active_organization_session_count(organization, exclude_session_key=None):
    cleanup_expired_organization_sessions(organization)
    qs = OrganizationSession.objects.filter(organization=organization)
    if exclude_session_key:
        qs = qs.exclude(session_key=exclude_session_key)
    return qs.count()


def can_organization_login(user, session_key=None):
    if not user or not user.is_organization:
        return True, ""
    org = get_user_organization(user)
    if org is None:
        return True, ""
    count = active_organization_session_count(org, exclude_session_key=session_key)
    if count >= MAX_ORGANIZATION_CONCURRENT_LOGINS:
        return (
            False,
            f"Maximum of {MAX_ORGANIZATION_CONCURRENT_LOGINS} accounts for this organization can be logged in at once.",
        )
    return True, ""


@transaction.atomic
def register_organization_session(user, session_key):
    if not user or not user.is_organization or not session_key:
        return None
    org = get_user_organization(user)
    if org is None:
        return None
    locked_org = Organization.objects.select_for_update().get(pk=org.pk)
    ok, msg = can_organization_login(user, session_key=session_key)
    if not ok:
        raise OrganizationSessionLimitError(msg)
    session_obj, _ = OrganizationSession.objects.update_or_create(
        session_key=session_key,
        defaults={
            "organization": locked_org,
            "user": user,
        },
    )
    return session_obj


def remove_organization_session(session_key):
    if not session_key:
        return
    OrganizationSession.objects.filter(session_key=session_key).delete()

