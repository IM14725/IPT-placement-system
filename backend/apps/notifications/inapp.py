"""In-app notifications: create the row, then push it live.

Every notification a user gets is a ``Notification`` row (shown on /notifications/ and in the header
bell). After the transaction commits, ``notifications.fanout`` publishes it on the ``ipt:notify`` Redis
channel so the FastAPI WebSocket delivers it to an open browser tab.
"""

from django.db import transaction

from apps.notifications.models import Notification, NotificationType


def notify(user, type, title, body, *, link="", actor=None, push=True):
    """Create one in-app notification for ``user`` and (after commit) push it live."""
    notification = Notification.objects.create(
        user=user,
        actor=actor,
        type=type,
        title=title[:200],
        body=body,
        link=(link or "")[:255],
    )
    if push:
        _push([notification.id])
    return notification


def notify_many(users, type, title, body, *, link="", actor=None):
    """The same notification for several users (e.g. every member who may review applicants)."""
    rows = [
        Notification(user=user, actor=actor, type=type, title=title[:200], body=body, link=(link or "")[:255])
        for user in users
    ]
    created = Notification.objects.bulk_create(rows)
    _push([n.id for n in created])
    return created


def _push(notification_ids):
    if not notification_ids:
        return

    def _send():
        from apps.notifications.tasks import fanout

        try:
            fanout.delay(notification_ids)
        except Exception:  # noqa: BLE001 - a broker outage must not break the request
            pass

    transaction.on_commit(_send)


def unread_count(user):
    return Notification.objects.filter(user=user, is_read=False).count()


def organization_recipients(organization, perm):
    """Active members of ``organization`` whose role grants ``perm``."""
    from apps.accounts.models import User
    from apps.companies.permissions import org_has_perm

    users = User.objects.filter(id__in=organization.member_user_ids(), is_active=True)
    return [u for u in users if org_has_perm(u, perm, organization)]


__all__ = [
    "Notification",
    "NotificationType",
    "notify",
    "notify_many",
    "unread_count",
    "organization_recipients",
]
