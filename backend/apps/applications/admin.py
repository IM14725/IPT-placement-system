from django.contrib import admin

from apps.applications.models import Application


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("id", "student", "slot", "company", "status", "created_at")
    list_filter = ("status", "slot__company", "slot__industry")
    search_fields = ("student__user__email", "slot__title", "slot__company__name")
    readonly_fields = ("created_at", "updated_at", "payment_ref")

    def company(self, obj):
        return obj.slot.company.name

    company.short_description = "Company"

    def payment_ref(self, obj):
        payment = getattr(obj, "payment", None)
        return payment.reference_id if payment else "-"

    payment_ref.short_description = "Payment reference"