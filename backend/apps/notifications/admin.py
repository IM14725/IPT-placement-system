from django.contrib import admin

from apps.notifications.models import Message, Notification, NotificationTemplate


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "type", "title", "is_read", "created_at")
    list_filter = ("type", "is_read", "created_at")
    search_fields = ("user__email", "title")


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("user", "channel", "status", "attempts", "subject", "created_at")
    list_filter = ("channel", "status", "created_at")
    search_fields = ("user__email", "body", "subject")


@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = ("key", "name", "channel", "trigger_label", "is_active", "updated_at")
    list_filter = ("channel", "is_active")
    search_fields = ("key", "name", "body")