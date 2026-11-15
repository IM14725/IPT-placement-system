from django.utils.translation import gettext_lazy
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.education import EducationLevel, AcademicYear, education_level_label
from apps.core.models import TimeStampedModel


class SlotStatus(models.TextChoices):
    OPEN = "OPEN", _("Open")
    FULL = "FULL", _("Full")
    PAUSED = "PAUSED", _("Paused")
    CLOSED = "CLOSED", _("Closed")


class Slot(TimeStampedModel):
    organization = models.ForeignKey(
        "companies.Organization",
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
    academic_year = models.PositiveSmallIntegerField(
        null=True, 
        blank=True,
        choices=AcademicYear.choices,
        help_text=gettext_lazy("Required if Higher Diploma is selected")
    )
    education_level = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        choices=EducationLevel.choices,
        help_text=gettext_lazy("Minimum TCU qualification level required (Level 4 Certificate to Level 10 PhD)."),
    )
    application_deadline = models.DateTimeField(
        null=True,
        blank=True,
        help_text=gettext_lazy("Optional. Students cannot start a new application after this time."),
    )
    start_date = models.DateField(
        null=True, blank=True, help_text=gettext_lazy("Optional. When the placement is expected to begin.")
    )
    duration_weeks = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(104)],
        help_text=gettext_lazy("Optional. Length of the placement in weeks."),
    )
    reporting_contact = models.CharField(
        max_length=300,
        blank=True,
        help_text=gettext_lazy("Who to ask for and where to report on day one. Shown only to accepted students."),
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
    def deadline_passed(self):
        from django.utils import timezone

        return bool(self.application_deadline and self.application_deadline <= timezone.now())

    @property
    def applications_closed(self):
        """Closed to new applications because the deadline passed (the status can still say OPEN)."""
        return self.deadline_passed and self.status in (SlotStatus.OPEN, SlotStatus.FULL)

    @property
    def end_date(self):
        from datetime import timedelta

        if self.start_date and self.duration_weeks:
            return self.start_date + timedelta(weeks=self.duration_weeks)
        return None

    @property
    def placement_label(self):
        """e.g. "Starts 5 Jan 2027 · 8 weeks" (empty when the organization gave neither)."""
        parts = []
        if self.start_date:
            parts.append(f"Starts {self.start_date.day} {self.start_date:%b %Y}")
        if self.duration_weeks:
            parts.append(f"{self.duration_weeks} week{'s' if self.duration_weeks != 1 else ''}")
        return " · ".join(parts)

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

    @property
    def requirement_label(self):
        """The qualification a slot asks for, e.g. "Bachelor's" or "Higher Diploma · First Year / Year 1"."""
        parts = []
        label = education_level_label(self.education_level)
        if label:
            parts.append(label)
        if self.academic_year:
            parts.append(self.get_academic_year_display())
        return " · ".join(parts)

    def refresh_status(self):
        # PAUSED and CLOSED are the organization's own decisions: seat counts only move a slot
        # between OPEN and FULL, so a cancellation can never silently re-open a paused slot.
        if self.status in (SlotStatus.PAUSED, SlotStatus.CLOSED):
            return
        if self.is_full:
            self.status = SlotStatus.FULL
        elif self.status == SlotStatus.FULL:
            self.status = SlotStatus.OPEN
        self.save(update_fields=["status", "updated_at"])

    def __str__(self):
        return f"{self.title} ({self.organization.name})"

class SlotView(TimeStampedModel):
    """One row per student who opened a slot's detail page: the "views" in the slot funnel.

    Unique per (slot, student), so reloading the page or coming back later does not inflate it.
    """

    slot = models.ForeignKey(Slot, on_delete=models.CASCADE, related_name="views")
    student = models.ForeignKey(
        "students.StudentProfile", on_delete=models.CASCADE, related_name="slot_views"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["slot", "student"], name="uniq_slot_student_view")
        ]

    def __str__(self):
        return f"{self.student_id} viewed {self.slot_id}"
