from functools import wraps
from urllib.parse import quote

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse

from apps.accounts import mfa


def hardware_reverify(view):
    """Require a fresh hardware-key confirmation before a high-risk (POST) admin action.

    The page itself (GET) is viewable; submitting it sends the admin to the step-up screen
    and back. Place below ``@login_required``.
    """

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if request.method == "POST" and mfa.enforced() and not mfa.hardware_recent(request):
            messages.warning(
                request, "This is a high-risk action. Confirm with your hardware security key, then submit again."
            )
            return redirect(f"{reverse('mfa-step-up')}?next={quote(request.get_full_path())}")
        return view(request, *args, **kwargs)

    return wrapper
