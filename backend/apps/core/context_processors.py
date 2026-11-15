from django.urls import reverse

from apps.core.thumbnails import thumbnail_url


def nav_user(request):
    """Provide the current user's profile link, photo, and display name for the navbar."""
    user = request.user
    if not user.is_authenticated:
        return {"nav_user": None}
    url = ""
    photo = ""
    if user.is_student:
        url = reverse("student-profile")
        profile = getattr(user, "student_profile", None)
        if profile and profile.profile_photo:
            photo = thumbnail_url(profile.profile_photo, 96)  # tiny navbar avatar, not the 2 MB original
    elif user.is_organization:
        url = reverse("organization-profile")
    else:
        url = reverse("platform-verifications")
    nav_org = {}
    if user.is_organization:
        from apps.companies.permissions import (
            APPLICANTS_ACCEPT,
            APPLICANTS_VIEW,
            AUDIT_VIEW,
            org_permissions,
        )

        perms = org_permissions(user)
        nav_org = {
            "applicants": APPLICANTS_VIEW in perms,
            "accept": APPLICANTS_ACCEPT in perms,
            "audit": AUDIT_VIEW in perms,
        }
    return {
        "nav_org": nav_org,
        "nav_user": {
            "url": url,
            "photo": photo,
            "name": user.full_name or user.email,
        },
        "emergency_session": user.role == "SUPER_ADMIN" and hasattr(user, "break_glass"),
    }


def tc_update_banner(request):
    """Return the T&C update banner text if the platform setting is active (T&C §14)."""
    from apps.core.models import PlatformSetting
    setting = PlatformSetting.objects.filter(key="tc_update_banner", value_bool=True).first()
    if setting and setting.value_text:
        return {"tc_update_banner": setting.value_text}
    return {"tc_update_banner": None}