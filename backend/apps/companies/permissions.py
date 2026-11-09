from functools import wraps

from django.http import HttpResponseForbidden

from apps.companies.models import (
    MembershipStatus,
    OrganizationMember,
    OrganizationMemberRole,
)
from apps.companies.services import get_user_organization

R = OrganizationMemberRole

# Permissions an organization member can hold. Owner is a superset of Manager,
# Manager a superset of Recruiter.
ORG_VIEW = "org.view"
PROFILE_EDIT = "profile.edit"
DOCUMENTS_MANAGE = "documents.manage"
SLOTS_VIEW = "slots.view"
SLOTS_MANAGE = "slots.manage"
APPLICANTS_VIEW = "applicants.view"
APPLICANTS_ACCEPT = "applicants.accept"
MEMBERS_MANAGE = "members.manage"
AUDIT_VIEW = "audit.view"

_RECRUITER = {ORG_VIEW, SLOTS_VIEW, SLOTS_MANAGE, APPLICANTS_VIEW}
_MANAGER = _RECRUITER | {PROFILE_EDIT, DOCUMENTS_MANAGE, APPLICANTS_ACCEPT, AUDIT_VIEW}
_OWNER = _MANAGER | {MEMBERS_MANAGE}

ROLE_PERMISSIONS = {
    R.OWNER: frozenset(_OWNER),
    R.MANAGER: frozenset(_MANAGER),
    R.RECRUITER: frozenset(_RECRUITER),
    # University roles are reserved for a later phase: read-only for now.
    R.UNIVERSITY_COORDINATOR: frozenset({ORG_VIEW}),
    R.UNIVERSITY_SUPERVISOR: frozenset({ORG_VIEW}),
}


def get_org_role(user, organization=None):
    """The user's active role inside ``organization`` (default: their organization)."""
    organization = organization or get_user_organization(user)
    if organization is None:
        return None
    if organization.user_id == user.id:
        return R.OWNER
    membership = OrganizationMember.objects.filter(
        organization=organization,
        user=user,
        membership_status=MembershipStatus.ACTIVE,
    ).first()
    return membership.role if membership else None


def org_permissions(user, organization=None):
    organization = organization or get_user_organization(user)
    if organization is None:
        # Legacy organization account with no organization yet: allow first-time setup.
        return ROLE_PERMISSIONS[R.OWNER] if getattr(user, "is_organization", False) else frozenset()
    return ROLE_PERMISSIONS.get(get_org_role(user, organization), frozenset())


def org_has_perm(user, perm, organization=None):
    if not getattr(user, "is_authenticated", False) or not user.is_organization:
        return False
    return perm in org_permissions(user, organization)


def org_permission_required(perm):
    """View decorator: the user must be an active member whose role grants ``perm``.

    Place below ``@login_required``. An organization that has not been created yet
    (registration stub) is treated like the owner so first-time setup still works.
    """

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_organization:
                return HttpResponseForbidden("Organization account required.")
            if perm not in org_permissions(request.user):
                return HttpResponseForbidden("Your organization role does not allow this action.")
            return view(request, *args, **kwargs)

        return wrapper

    return decorator
