from django.contrib import admin

from apps.applications.models import Application


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("id", "student", "slot", "organization", "status", "created_at")
    list_filter = ("status", "slot__organization", "slot__industry")
    search_fields = ("student__user__email", "slot__title", "slot__organization__name")
    readonly_fields = ("created_at", "updated_at", "payment_ref")

    def organization(self, obj):
        return obj.slot.organization.name

    organization.short_description = "Organization"

    def payment_ref(self, obj):
        payment = getattr(obj, "payment", None)
        return payment.reference_id if payment else "-"

    payment_ref.short_description = "Payment reference"