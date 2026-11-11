"""Server-side session policy.

* 10-minute idle lifetime (``SESSION_COOKIE_AGE``), enforced here and by the session row's own
  expiry, so it holds when the browser is closed or offline. Real user activity (the page
  reports mouse/keyboard/touch movement to ``session_extend``) restarts the 10 minutes; polling
  never does. A hard ceiling from login (``SESSION_ABSOLUTE_MAX_SECONDS``) still applies.
* One live session per account: logging in on a new device destroys the previous session
  row (``User.current_session_key``) and pushes a ``session_revoked`` event to the old tabs.
* Logout destroys the session on the server; authenticated responses are never cached.
* The backend alone decides whether a session is valid; the realtime push is only a courtesy.
"""

import time

from django.conf import settings
from django.contrib.auth import logout
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.contrib.sessions.models import Session
from django.db import transaction
from django.utils.cache import add_never_cache_headers

LOGIN_AT_KEY = "login_at"
ACTIVITY_AT_KEY = "active_at"


def _publish_revoked(user_id, reason):
    try:
        from apps.core.redis_client import publish

        publish("ipt:notify", {"user_id": str(user_id), "type": "session_revoked", "reason": reason})
    except Exception:  # noqa: BLE001 - the push is best effort; the server already decided
        pass


def _drop_session(key):
    if not key:
        return
    from apps.companies.services import remove_organization_session

    Session.objects.filter(session_key=key).delete()
    remove_organization_session(key)


def revoke_user_session(user, reason="revoked"):
    """Destroy the account's live session (role change, removal, suspension, password reset)."""
    from apps.accounts.models import User

    key = User.objects.filter(pk=user.pk).values_list("current_session_key", flat=True).first()
    _drop_session(key)
    User.objects.filter(pk=user.pk).update(current_session_key="")
    user.current_session_key = ""
    _publish_revoked(user.pk, reason)


def sync_session_key(request, old_key=None):
    """Record the session's current key as the account's one live session."""
    from apps.accounts.models import User
    from apps.companies.models import OrganizationSession

    new = request.session.session_key
    if not new:
        request.session.save()
        new = request.session.session_key
    previous = request.user.current_session_key
    User.objects.filter(pk=request.user.pk).update(current_session_key=new)
    request.user.current_session_key = new
    for old in {old_key, previous} - {None, "", new}:
        OrganizationSession.objects.filter(session_key=old).update(session_key=new)
    return new


def rotate_session_key(request):
    """New session id for the same login (privilege elevation / MFA step-up: anti-fixation)."""
    old = request.session.session_key
    request.session.cycle_key()
    return sync_session_key(request, old_key=old)


def seconds_remaining(request):
    """Time left before the session ends: the idle window or the hard ceiling, whichever is first."""
    login_at = request.session.get(LOGIN_AT_KEY)
    if login_at is None:
        return 0
    now = time.time()
    active_at = request.session.get(ACTIVITY_AT_KEY, login_at)
    idle_left = settings.SESSION_COOKIE_AGE - (now - active_at)
    ceiling_left = settings.SESSION_ABSOLUTE_MAX_SECONDS - (now - login_at)
    return max(0, int(min(idle_left, ceiling_left)))


def touch(request):
    """Restart the idle window. Only called for real user activity, never for background polling."""
    request.session[ACTIVITY_AT_KEY] = int(time.time())
    request.session.set_expiry(settings.SESSION_COOKIE_AGE)


def _on_login(sender, request, user, **kwargs):
    from apps.accounts.models import User
    from apps.core.audit import audit_event

    if request is None:
        return
    if not request.session.session_key:
        request.session.save()
    new_key = request.session.session_key
    request.session[LOGIN_AT_KEY] = int(time.time())
    request.session[ACTIVITY_AT_KEY] = request.session[LOGIN_AT_KEY]
    request.session.set_expiry(settings.SESSION_COOKIE_AGE)

    revoked_other = False
    with transaction.atomic():
        old = User.objects.select_for_update().filter(pk=user.pk).values_list(
            "current_session_key", flat=True
        ).first()
        if old and old != new_key:
            _drop_session(old)
            revoked_other = True
        User.objects.filter(pk=user.pk).update(current_session_key=new_key)
    user.current_session_key = new_key
    if revoked_other:
        _publish_revoked(user.pk, "signed_in_elsewhere")

    org = _org_for(user)
    audit_event(
        request,
        action="Login",
        module="Security",
        actor=user,
        organization=org,
        description="Signed in" + (" (previous device signed out)" if revoked_other else ""),
        context={"replaced_other_session": revoked_other},
    )


def _on_logout(sender, request, user, **kwargs):
    from apps.accounts.models import User
    from apps.core.audit import audit_event

    if user is None or request is None:
        return
    key = request.session.session_key
    if key and key == getattr(user, "current_session_key", None):
        User.objects.filter(pk=user.pk).update(current_session_key="")
        user.current_session_key = ""
    audit_event(
        request,
        action="Logout",
        module="Security",
        actor=user,
        organization=_org_for(user),
        description="Signed out",
    )
    _publish_revoked(user.pk, "logout")


def _org_for(user):
    if not getattr(user, "is_organization", False):
        return None
    from apps.companies.models import MembershipStatus, OrganizationMember
    from apps.companies.services import get_user_organization

    org = get_user_organization(user)
    if org is not None:
        return org
    member = (
        OrganizationMember.objects.filter(user=user)
        .exclude(membership_status=MembershipStatus.INVITED)
        .select_related("organization")
        .first()
    )
    return member.organization if member else None


def connect_signals():
    user_logged_in.connect(_on_login, dispatch_uid="accounts_session_login")
    user_logged_out.connect(_on_logout, dispatch_uid="accounts_session_logout")


class SessionPolicyMiddleware:
    """Reject expired or superseded sessions on every request, and never cache private pages."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            key = request.session.session_key
            login_at = request.session.get(LOGIN_AT_KEY)
            now = time.time()
            active_at = request.session.get(ACTIVITY_AT_KEY, login_at)
            expired = (
                login_at is None
                or (now - active_at) > settings.SESSION_COOKIE_AGE
                or (now - login_at) > settings.SESSION_ABSOLUTE_MAX_SECONDS
            )
            superseded = user.current_session_key != key
            if expired or superseded:
                logout(request)
                request.user = AnonymousUser()
        response = self.get_response(request)
        if getattr(request, "user", None) is not None and request.user.is_authenticated:
            add_never_cache_headers(response)
            response["Cache-Control"] = "no-store, private, max-age=0"
        return response
