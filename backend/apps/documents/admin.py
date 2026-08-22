from django.contrib import admin
from django.utils.html import format_html

from apps.documents.models import Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("owner", "doc_type", "original_name", "size_bytes", "is_verified", "uploaded_at")
    list_filter = ("doc_type", "is_verified", "uploaded_at")
    search_fields = ("owner__email", "original_name")
    readonly_fields = ("sha256", "size_bytes", "uploaded_at", "file_link")

    def file_link(self, obj):
        return format_html('<a href="{}" target="_blank">Open file</a>', obj.file.url)

    file_link.short_description = "File"