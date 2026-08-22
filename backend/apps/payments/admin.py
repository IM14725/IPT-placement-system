from django.contrib import admin

from apps.payments.models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "reference_id",
        "student",
        "company",
        "amount",
        "currency",
        "method",
        "gateway",
        "status",
        "is_paid",
        "paid_at",
    )
    list_filter = ("status", "method", "gateway", "currency")
    search_fields = ("reference_id", "gateway_txn_id", "student__user__email")
    readonly_fields = ("reference_id", "created_at", "updated_at", "paid_at", "callback_payload")

    def company(self, obj):
        return obj.application.slot.company.name

    company.short_description = "Company"