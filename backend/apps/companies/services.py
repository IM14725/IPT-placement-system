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


def organization_email_taken(email):
    return Organization.objects.filter(email__iexact=(email or "").strip()).exists()


def create_organization(
    user,
    name,
    organization_type=OrganizationType.COMPANY,
    email="",
    phone="",
    position="",
    brela_number="",
    tin="",
):
    """Create the organization and its first member, who becomes the Manager (Owner).

    ``email``/``phone`` are the organization's official contact identity; the registering
    user keeps their own personal login email and phone.
    """
    org = Organization.objects.create(
        user=user,
        name=(name or "").strip(),
        organization_type=organization_type,
        email=(email or "").strip().lower() or getattr(user, "email", "") or "",
        phone=phone or getattr(user, "phone", "") or "",
        industry="",
        brela_number=brela_number or "",
        tin=tin or "",
    )
    member, _ = OrganizationMember.objects.get_or_create(
        organization=org,
        user=user,
        defaults={
            "role": OrganizationMemberRole.OWNER,
            "membership_status": MembershipStatus.ACTIVE,
            "position": (position or "").strip(),
        },
    )
    if position and not member.position:
        member.position = position.strip()
        member.save(update_fields=["position"])
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
    organization,
    email,
    password,
    role=OrganizationMemberRole.RECRUITER,
    phone="",
    first_name="",
    last_name="",
    position="",
    added_by=None,
    request=None,
):
    """Create a staff account with its own credentials (max 5 active accounts per organization).

    The temporary password must be replaced at first login, and the member verifies their own
    email by OTP, so the Manager never holds a working credential for them.
    """
    from apps.accounts.models import normalize_phone
    from apps.core.audit import audit_event

    locked_org = Organization.objects.select_for_update().get(pk=organization.pk)
    active_count = OrganizationMember.objects.filter(
        organization=locked_org, membership_status=MembershipStatus.ACTIVE
    ).count()
    if active_count >= MAX_ORGANIZATION_ACCOUNTS:
        raise ValidationError(
            f"An organization can have at most {MAX_ORGANIZATION_ACCOUNTS} active accounts."
        )
    if role == OrganizationMemberRole.OWNER:
        raise ValidationError("An organization has a single Manager (Owner): the registering user.")
    email_norm = (email or "").strip().lower()
    if not email_norm:
        raise ValidationError("Email address is required.")
    if User.objects.filter(email=email_norm).exists():
        raise ValidationError(
            "This email address is already associated with an account. Please use another email address."
        )
    phone_norm = normalize_phone(phone)
    if phone_norm and User.objects.filter(phone=phone_norm).exists():
        raise ValidationError(
            "This phone number is already associated with an account. Please use another phone number."
        )
    user = User.objects.create_user(
        email=email_norm,
        password=password,
        role=UserRole.ORGANIZATION,
        phone=phone_norm,
        first_name=(first_name or "").strip(),
        last_name=(last_name or "").strip(),
        must_change_password=True,
    )
    member = OrganizationMember.objects.create(
        organization=locked_org,
        user=user,
        role=role,
        membership_status=MembershipStatus.ACTIVE,
        position=(position or "").strip(),
    )
    audit_event(
        request,
        action="Member Added",
        module="Organization Team",
        actor=added_by,
        organization=locked_org,
        target=user,
        target_label=user.email,
        description=f"Added {user.email} as {member.get_role_display()}",
        context={"role": role, "position": member.position},
    )
    return member


@transaction.atomic
def remove_organization_member(organization, member, removed_by, reason, request=None):
    """Revoke a member's access but keep the membership, the account and all their history."""
    from apps.accounts.sessions import revoke_user_session
    from apps.core.audit import audit_event

    if member.organization_id != organization.id:
        raise ValidationError("That member does not belong to this organization.")
    if member.user_id == organization.user_id or member.user_id == removed_by.id:
        raise ValidationError("The Manager (Owner) account cannot be removed.")
    if member.membership_status == MembershipStatus.REMOVED:
        raise ValidationError("This member has already been removed.")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Please give a reason for removing this member.")
    previous_role = member.role
    member.membership_status = MembershipStatus.REMOVED
    member.removed_at = timezone.now()
    member.removed_by = removed_by
    member.removal_reason = reason
    member.save()
    user = member.user
    user.is_active = False
    user.save(update_fields=["is_active"])
    remove_organization_session_for_user(user)
    revoke_user_session(user, "removed")
    audit_event(
        request,
        action="Member Removed",
        module="Organization Team",
        actor=removed_by,
        organization=organization,
        target=user,
        target_label=user.email,
        description=f"Removed {user.email}: {reason}",
        context={"previous_role": previous_role, "reason": reason},
    )
    return member


@transaction.atomic
def change_member_role(organization, member, new_role, changed_by, request=None):
    """Change a member's role. Their session is destroyed so they sign in again with a fresh id."""
    from apps.accounts.sessions import revoke_user_session
    from apps.core.audit import audit_event

    if member.organization_id != organization.id or member.membership_status != MembershipStatus.ACTIVE:
        raise ValidationError("That member is not an active member of this organization.")
    if member.user_id == organization.user_id:
        raise ValidationError("The Manager (Owner) role cannot be changed.")
    if new_role not in (OrganizationMemberRole.MANAGER, OrganizationMemberRole.RECRUITER):
        raise ValidationError("Choose a valid role.")
    old_role = member.role
    if old_role == new_role:
        return member
    member.role = new_role
    member.save()
    revoke_user_session(member.user, "role_changed")
    audit_event(
        request,
        action="Member Role Changed",
        module="Organization Team",
        actor=changed_by,
        organization=organization,
        target=member.user,
        target_label=member.user.email,
        description=f"{member.user.email}: {old_role} -> {new_role}",
        context={"old_role": old_role, "new_role": new_role},
    )
    return member


def remove_organization_session_for_user(user):
    OrganizationSession.objects.filter(user=user).delete()


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


def active_organization_session_count(organization, exclude_session_key=None, exclude_user=None):
    cleanup_expired_organization_sessions(organization)
    qs = OrganizationSession.objects.filter(organization=organization)
    if exclude_session_key:
        qs = qs.exclude(session_key=exclude_session_key)
    if exclude_user is not None:
        # A member signing in again replaces their own session, so it never counts against the cap.
        qs = qs.exclude(user=exclude_user)
    return qs.count()


def can_organization_login(user, session_key=None):
    if not user or not user.is_organization:
        return True, ""
    org = get_user_organization(user)
    if org is None:
        return True, ""
    count = active_organization_session_count(org, exclude_session_key=session_key, exclude_user=user)
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
    # One session per person: drop this member's older session row(s) for the organization.
    OrganizationSession.objects.filter(user=user).exclude(session_key=session_key).delete()
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

