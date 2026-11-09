"""Steps a signed-in user must complete before using anything else.

1. Replace a temporary password.
2. Administrators: enroll MFA (a hardware key for Super Admins), pass the second factor for
   this session, and hold a set of recovery codes (regenerated after one is used).
"""

from django.shortcuts import redirect

from apps.accounts import mfa
from apps.accounts.models import RecoveryCode

ALWAYS_ALLOWED = (
    "/auth/logout/",
    "/auth/session-status/",
    "/static/",
    "/i18n/",
)


def required_step(request):
    """Return ``(url_name, allowed_path_prefixes)`` for the step the user is stuck on, or None."""
    user = request.user
    if user.must_change_password:
        return "change-password", ("/auth/change-password/",)
    if mfa.enforced() and mfa.is_admin_user(user):
        if not mfa.mfa_enrolled(user):
            return "mfa-settings", ("/auth/mfa/",)
        if not request.session.get(mfa.MFA_OK):
            return "mfa-challenge", ("/auth/mfa/",)
        if request.session.get(mfa.RECOVERY_USED) or not RecoveryCode.objects.filter(user=user).exists():
            return "mfa-settings", ("/auth/mfa/",)
    return None


class AccountGateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            step = required_step(request)
            if step is not None:
                name, allowed = step
                path = request.path
                if not path.startswith(ALWAYS_ALLOWED + allowed):
                    return redirect(name)
        return self.get_response(request)
