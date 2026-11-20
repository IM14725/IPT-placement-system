from celery import shared_task


@shared_task(name="notifications.fanout")
def fanout(notification_ids):
    """Publish notification payloads to Redis so FastAPI WS pushes them live."""
    from apps.core.redis_client import publish
    from apps.notifications.models import Notification

    for notification in Notification.objects.filter(id__in=notification_ids).select_related("user"):
        publish(
            "ipt:notify",
            {
                "user_id": notification.user_id,
                "notification_id": notification.id,
                "type": notification.type,
                "title": notification.title,
                "body": notification.body,
                "link": notification.link,
                "created_at": notification.created_at.isoformat(),
            },
        )
    return {"published": len(notification_ids)}


@shared_task(name="notifications.purge_old_read")
def purge_old_read():
    """Retention: delete notifications the user already read and that are older than
    NOTIFICATION_RETENTION_DAYS. Unread ones are never touched. Deleted in small batches so a
    big backlog never holds a long lock (the table is hit by every page's bell)."""
    from datetime import timedelta

    from django.conf import settings
    from django.utils import timezone

    from apps.notifications.models import Notification

    cutoff = timezone.now() - timedelta(days=settings.NOTIFICATION_RETENTION_DAYS)
    deleted = 0
    while True:
        ids = list(
            Notification.objects.filter(is_read=True, created_at__lt=cutoff).values_list("id", flat=True)[:1000]
        )
        if not ids:
            break
        deleted += Notification.objects.filter(id__in=ids).delete()[0]
    return {"deleted": deleted}