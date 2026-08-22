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