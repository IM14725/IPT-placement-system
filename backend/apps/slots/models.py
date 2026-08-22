from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.education import EducationLevel
from apps.core.models import TimeStampedModel


class SlotStatus(models.TextChoices):
    OPEN = "OPEN", _("Open")
    FULL = "FULL", _("Full")
    PAUSED = "PAUSED", _("Paused")
    CLOSED = "CLOSED", _("Closed")


class Slot(TimeStampedModel):
    company = models.ForeignKey(
        "companies.CompanyProfile",
        on_delete=models.CASCADE,
        related_name="slots",
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    industry = models.CharField(max_length=150)
    role_type = models.CharField(max_length=150)
    district = models.ForeignKey(
        "locations.District",
        on_delete=models.CASCADE,
        related_name="slots",
    )
    street = models.CharField(max_length=255, blank=True)
    department = models.CharField(max_length=200, blank=True)
    level = models.PositiveSmallIntegerField(null=True, blank=True)
    education_level = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        choices=EducationLevel.choices,
        help_text="Minimum TCU qualification level required (Level 4 Certificate to Level 10 PhD).",
    )
    capacity = models.PositiveSmallIntegerField(default=1)
    stipend_available = models.BooleanField(default=False)
    stipend_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    skills_required = models.JSONField(default=list, blank=True)
    status = models.CharField(
        max_length=10, choices=SlotStatus.choices, default=SlotStatus.OPEN
    )

    class Meta:
        ordering = ["-created_at"]

    @property
    def region(self):
        return self.district.region

    @property
    def booked_count(self):
        from apps.applications.models import Application, ApplicationStatus

        return Application.objects.filter(
            slot=self,
            status__in=[ApplicationStatus.PENDING, ApplicationStatus.PAID],
        ).count()

    @property
    def available_count(self):
        return max(0, self.capacity - self.booked_count)

    @property
    def is_full(self):
        return self.available_count <= 0

    def refresh_status(self):
        if self.is_full:
            self.status = SlotStatus.FULL
        elif self.status == SlotStatus.FULL:
            self.status = SlotStatus.OPEN
        self.save(update_fields=["status", "updated_at"])

    def __str__(self):
        return f"{self.title} ({self.company.name})"