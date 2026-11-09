from rest_framework.permissions import BasePermission


class IsStudent(BasePermission):
    message = "A student account is required."

    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and request.user.is_student
        )


class IsOrganization(BasePermission):
    message = "An organization account is required."

    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and request.user.is_organization
        )


class IsPlatformAdmin(BasePermission):
    message = "A platform admin account is required."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_platform_admin
        )


class IsPlatformSuperAdmin(BasePermission):
    message = "A platform super admin account is required."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_super_admin
        )


class IsVerifiedStudent(IsStudent):
    message = "Your student profile must be verified by the platform admin."

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        return request.user.student_profile.is_verified


class IsApprovedOrganization(IsOrganization):
    message = "Your organization profile must be approved by the platform admin."

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        from apps.companies.services import get_user_organization

        org = get_user_organization(request.user)
        return bool(org and org.is_approved)


class IsOrganizationOwner(BasePermission):
    """Object-level: the organization must own the object (slot/application)."""

    def has_object_permission(self, request, view, obj):
        from apps.companies.services import get_user_organization

        organization = get_user_organization(request.user)
        if organization is None:
            return False
        if hasattr(obj, "organization"):
            return obj.organization_id == organization.id
        if hasattr(obj, "slot"):
            return obj.slot.organization_id == organization.id
        return False