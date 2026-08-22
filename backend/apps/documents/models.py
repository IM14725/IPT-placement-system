from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from .validators import validate_upload


def document_upload_path(instance, filename):
    return f"documents/{instance.owner_id}/{instance.doc_type}/{filename}"


class Document(models.Model):
    class DocType(models.TextChoices):
        STUDENT_ID = "STUDENT_ID", _("Student ID Card")
        RESULTS_MATRIX = "RESULTS_MATRIX", _("Semester Results Matrix")
        CV = "CV", _("Curriculum Vitae")
        INTRO_LETTER = "INTRO_LETTER", _("University Introduction Letter")
        BRELA_CERT = "BRELA_CERT", _("BRELA Registration Certificate")
        TIN_CERT = "TIN_CERT", _("TIN Certificate")
        BUSINESS_LICENSE = "BUSINESS_LICENSE", _("Business License")

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="documents",
    )
    doc_type = models.CharField(max_length=30, choices=DocType.choices)
    file = models.FileField(upload_to=document_upload_path)
    original_name = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=120, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    is_verified = models.BooleanField(default=False)
    scan_status = models.CharField(
        max_length=20,
        choices=[("PENDING", "Pending"), ("CLEAN", "Clean"), ("ERROR", "Error")],
        default="PENDING",
        help_text="Result of the async deep file scan (Celery).",
    )
    scan_error = models.CharField(max_length=255, blank=True, default="")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def clean(self):
        if self.file:
            self.sha256 = validate_upload(self.file)
            self.size_bytes = self.file.size
            wrapped = getattr(self.file, "file", None)
            self.mime_type = getattr(wrapped, "content_type", "") or ""

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_doc_type_display()} ({self.original_name})"