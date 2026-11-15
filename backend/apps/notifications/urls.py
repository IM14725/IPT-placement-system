from django.urls import path

from apps.notifications import views

urlpatterns = [
    path("", views.notification_list, name="notifications"),
    path("summary/", views.notification_summary, name="notifications-summary"),
    path("read-all/", views.notification_mark_all, name="notifications-read-all"),
    path("clear-read/", views.notification_clear_read, name="notifications-clear-read"),
    path("<uuid:notification_id>/open/", views.notification_open, name="notification-open"),
    path("<uuid:notification_id>/mark/", views.notification_mark, name="notification-mark"),
    path("<uuid:notification_id>/delete/", views.notification_delete, name="notification-delete"),
]
