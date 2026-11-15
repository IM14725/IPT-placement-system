"""The in-app notification centre: one page to read, filter and clear notifications, plus the small
JSON summary the header bell polls. Every query is scoped to ``request.user``; another user's
notification id is a 404, never an action."""

from django.utils.translation import gettext
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.core.pagination import paginate
from apps.notifications.models import Notification, NotificationType


def _safe_link(request, link):
    """Only follow links that stay on this site."""
    if link and url_has_allowed_host_and_scheme(link, allowed_hosts={request.get_host()}):
        return link
    return ""


def _back(request):
    target = request.POST.get("next") or ""
    return redirect(_safe_link(request, target) or "notifications")


@login_required
def notification_list(request):
    base = Notification.objects.filter(user=request.user)
    status = request.GET.get("status", "")
    type_ = request.GET.get("type", "")
    qs = base
    if status == "unread":
        qs = qs.filter(is_read=False)
    elif status == "read":
        qs = qs.filter(is_read=True)
    if type_ in NotificationType.values:
        qs = qs.filter(type=type_)
    page_obj = paginate(qs.order_by("-created_at"), request.GET.get("page"))
    alerts, max_alerts = [], 0
    if request.user.is_student:
        # Slot alerts (saved searches) are managed on this same page.
        from apps.students.models import MAX_SAVED_SEARCHES, SavedSearch, StudentProfile

        max_alerts = MAX_SAVED_SEARCHES
        profile = StudentProfile.objects.filter(user=request.user).first()
        if profile:
            alerts = list(SavedSearch.objects.filter(student=profile).select_related("region", "district__region"))
    return render(
        request,
        "notifications/list.html",
        {
            "alerts": alerts,
            "max_alerts": max_alerts,
            "show_alerts": request.user.is_student,
            "notifications": page_obj,
            "page_obj": page_obj,
            "querystring": urlencode({k: v for k, v in request.GET.items() if k != "page"}),
            "status": status,
            "selected_type": type_,
            "types": NotificationType.choices,
            "unread_total": base.filter(is_read=False).count(),
            "read_total": base.filter(is_read=True).count(),
            "total": base.count(),
        },
    )


@login_required
def notification_open(request, notification_id):
    """Mark as read, then go where the notification points (or back to the list)."""
    notification = get_object_or_404(Notification, id=notification_id, user=request.user)
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=["is_read", "updated_at"])
    return redirect(_safe_link(request, notification.link) or "notifications")


@login_required
@require_POST
def notification_mark(request, notification_id):
    notification = get_object_or_404(Notification, id=notification_id, user=request.user)
    notification.is_read = request.POST.get("read", "1") != "0"
    notification.save(update_fields=["is_read", "updated_at"])
    return _back(request)


@login_required
@require_POST
def notification_mark_all(request):
    updated = Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    if updated:
        messages.success(request, gettext('%(updated)s notification(s) marked as read.') % {"updated": updated})
    return _back(request)


@login_required
@require_POST
def notification_delete(request, notification_id):
    get_object_or_404(Notification, id=notification_id, user=request.user).delete()
    return _back(request)


@login_required
@require_POST
def notification_clear_read(request):
    deleted, _ = Notification.objects.filter(user=request.user, is_read=True).delete()
    if deleted:
        messages.success(request, gettext('%(deleted)s read notification(s) removed.') % {"deleted": deleted})
    return _back(request)


@login_required
def notification_summary(request):
    """Unread count + the latest five, for the header bell."""
    base = Notification.objects.filter(user=request.user)
    latest = [
        {
            "id": str(n.id),
            "title": n.title,
            "body": n.body[:140],
            "is_read": n.is_read,
            "created_at": n.created_at.isoformat(),
            "url": f"/notifications/{n.id}/open/",
        }
        for n in base.order_by("-created_at")[:5]
    ]
    response = JsonResponse({"unread": base.filter(is_read=False).count(), "latest": latest})
    response["Cache-Control"] = "no-store"
    return response
