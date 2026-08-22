from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from apps.companies.models import CompanyProfile
from apps.documents.models import Document
from apps.core.admin_mixins import VerificationAdminMixin


@admin.register(CompanyProfile)
class CompanyProfileAdmin(VerificationAdminMixin, admin.ModelAdmin):
    REQUIRED_DOC_TYPES = (
        Document.DocType.BRELA_CERT,
        Document.DocType.TIN_CERT,
        Document.DocType.BUSINESS_LICENSE,
    )
    list_display = (
        "name",
        "industry",
        "user",
        "view_documents",
        "verification_status",
        "reviewed_at",
    )
    list_filter = ("verification_status", "industry")
    search_fields = ("name", "user__email", "industry")
    list_editable = ("verification_status",)
    readonly_fields = ("created_at", "updated_at", "reviewed_at")
    fieldsets = (
        (None, {"fields": ("user", "name", "industry", "description")}),
        ("Location", {"fields": ("region", "district", "street")}),
        ("Verification", {"fields": ("verification_status", "rejection_reason", "reviewed_by", "reviewed_at")}),
    )

    @admin.display(description="Documents")
    def view_documents(self, obj):
        url = reverse("admin:documents_document_changelist") + f"?owner__id__exact={obj.user_id}"
        return format_html('<a href="{}">View</a>', url)