from rest_framework.permissions import BasePermission


class IsStudent(BasePermission):
    message = "A student account is required."

    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and request.user.is_student
        )


class IsCompany(BasePermission):
    message = "A company account is required."

    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and request.user.is_company
        )


class IsPlatformAdmin(BasePermission):
    message = "A platform admin account is required."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_platform_admin
        )


class IsVerifiedStudent(IsStudent):
    message = "Your student profile must be verified by the platform admin."

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        return request.user.student_profile.is_verified


class IsApprovedCompany(IsCompany):
    message = "Your company profile must be approved by the platform admin."

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        return request.user.company_profile.is_approved


class IsCompanyOwner(BasePermission):
    """Object-level: the company must own the object (slot/application)."""

    def has_object_permission(self, request, view, obj):
        company = getattr(request.user, "company_profile", None)
        if company is None:
            return False
        if hasattr(obj, "company"):
            return obj.company_id == company.id
        if hasattr(obj, "slot"):
            return obj.slot.company_id == company.id
        return False