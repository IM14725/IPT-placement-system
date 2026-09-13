from django.urls import reverse


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
            photo = profile.profile_photo.url
    elif user.is_company:
        url = reverse("company-profile")
    else:
        url = reverse("platform-verifications")
    return {
        "nav_user": {
            "url": url,
            "photo": photo,
            "name": user.full_name or user.email,
        }
    }


def tc_update_banner(request):
    """Return the T&C update banner text if the platform setting is active (T&C §14)."""
    from apps.core.models import PlatformSetting
    setting = PlatformSetting.objects.filter(key="tc_update_banner", value_bool=True).first()
    if setting and setting.value_text:
        return {"tc_update_banner": setting.value_text}
    return {"tc_update_banner": None}