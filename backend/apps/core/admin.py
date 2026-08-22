from django.contrib import admin

from apps.core.models import AuditLog, LedgerSnapshot, PlatformSetting


@admin.register(LedgerSnapshot)
class LedgerSnapshotAdmin(admin.ModelAdmin):
    list_display = ("created_at", "data")
    readonly_fields = ("created_at",)


@admin.register(PlatformSetting)
class PlatformSettingAdmin(admin.ModelAdmin):
    list_display = ("key", "label", "value_type", "get_value", "updated_at")
    list_filter = ("value_type",)
    search_fields = ("key", "label")
    readonly_fields = ("updated_at",)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor_label", "action", "module", "ip_address")
    list_filter = ("module", "is_system")
    search_fields = ("actor_label", "action", "description")
    readonly_fields = ("created_at",)