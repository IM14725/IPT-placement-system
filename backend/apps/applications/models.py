from pathlib import Path
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.documents.validators import validate_upload


def application_letter_upload_path(instance, filename):
    safe_name = Path(filename or "letter.pdf").name
    return f"application_letters/{instance.student_id}/{safe_name}"


class ApplicationStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    PAID = "PAID", _("Paid")
    UNPAID = "UNPAID", _("Unpaid")
    CANCELLED = "CANCELLED", _("Cancelled")


PAYMENT_DEADLINE_HOURS = 3


class Application(TimeStampedModel):
    student = models.ForeignKey(
        "students.StudentProfile",
        on_delete=models.CASCADE,
        related_name="applications",
    )
    slot = models.ForeignKey(
        "slots.Slot",
        on_delete=models.CASCADE,
        related_name="applications",
    )
    status = models.CharField(
        max_length=20,
        choices=ApplicationStatus.choices,
        default=ApplicationStatus.PENDING,
    )
    can_cancel = models.BooleanField(default=True)
    payment_deadline = models.DateTimeField(null=True, blank=True)
    is_accepted = models.BooleanField(default=False)
    company_message = models.TextField(blank=True)
    student_message = models.TextField(blank=True)
    application_letter = models.FileField(
        upload_to=application_letter_upload_path,
        validators=[validate_upload],
        blank=True,
        null=True,
    )
    letter_original_name = models.CharField(max_length=255, blank=True)
    letter_sha256 = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "slot"], name="uniq_student_slot_application"
            )
        ]

    @property
    def is_paid(self):
        if self.status == ApplicationStatus.PAID:
            return True
        payment = getattr(self, "payment", None)
        return payment is not None and payment.is_paid

    @property
    def is_unpaid(self):
        return self.status == ApplicationStatus.UNPAID

    @property
    def timeline_steps(self):
        paid = self.status == ApplicationStatus.PAID
        accepted = self.is_accepted
        return [
            {"key": "paid", "label": "Paid", "done": paid},
            {"key": "accepted", "label": "Accepted", "done": accepted},
        ]

    @property
    def is_cancellable(self):
        from django.utils import timezone
        from datetime import timedelta
        if not self.can_cancel or self.status != ApplicationStatus.PAID or self.is_accepted:
            return False
        if not hasattr(self, 'payment') or not self.payment or not self.payment.paid_at:
            return False
        return timezone.now() <= self.payment.paid_at + timedelta(hours=24)

    @property
    def current_letter(self):
        """The acceptance letter the student holds now (``None`` until the organization uploads one)."""
        cached = getattr(self, "_current_letter", False)
        if cached is not False:
            return cached
        self._current_letter = self.acceptance_letters.filter(is_current=True).first()
        return self._current_letter

    @property
    def can_replace_letter(self):
        letter = self.current_letter
        return bool(self.is_accepted and letter and letter.version < AcceptanceLetter.MAX_VERSIONS)

    def accept(self):
        self.is_accepted = True
        self.save(update_fields=["is_accepted", "updated_at"])

    @property
    def cancel_deadline(self):
        """When the 24h cancellation window closes (None when it does not apply)."""
        from datetime import timedelta

        payment = getattr(self, "payment", None)
        if not payment or not payment.paid_at:
            return None
        return payment.paid_at + timedelta(hours=24)

    @property
    def progress_steps(self):
        """Applied -> Paid -> Waiting for acceptance -> Accepted. There is no rejected step:
        an organization cannot reject a paid application."""
        paid = self.status == ApplicationStatus.PAID
        return [
            {"key": "applied", "label": _("Applied"), "done": True},
            {"key": "paid", "label": _("Paid"), "done": paid,
             "current": self.status == ApplicationStatus.PENDING},
            {"key": "review", "label": _("Waiting for acceptance"), "short": _("Waiting"), "done": paid and self.is_accepted,
             "current": paid and not self.is_accepted},
            {"key": "accepted", "label": _("Accepted"), "done": paid and self.is_accepted},
        ]

    def __str__(self):
        return f"Application #{self.pk} - {self.student} -> {self.slot}"


def acceptance_letter_upload_path(instance, filename):
    return f"acceptance_letters/{instance.application_id}/v{instance.version}-{instance.id}.pdf"


class AcceptanceLetter(TimeStampedModel):
    """The letter an organization uploads when it accepts an applicant (PDF only).

    The stored file is the *stamped* copy: a verification QR code was added and the PDF locked against
    editing before it was saved and emailed. A letter is never edited or deleted (database trigger); a
    correction is a second version, which marks the first one superseded. ``sha256`` is the fingerprint
    of the stored file, re-checked on every download and by the public check-a-file page.
    """

    MAX_VERSIONS = 2  # the original plus one replacement

    application = models.ForeignKey(Application, on_delete=models.PROTECT, related_name="acceptance_letters")
    version = models.PositiveSmallIntegerField(default=1)
    file = models.FileField(upload_to=acceptance_letter_upload_path)
    original_name = models.CharField(max_length=255, blank=True)
    sha256 = models.CharField(max_length=64)
    source_sha256 = models.CharField(max_length=64, help_text="Fingerprint of the PDF as the organization uploaded it.")
    page_count = models.PositiveSmallIntegerField(default=1)
    uploaded_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    uploaded_by_label = models.CharField(max_length=200, blank=True)
    is_current = models.BooleanField(default=True)
    superseded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(fields=["application", "version"], name="uniq_letter_version"),
            models.UniqueConstraint(
                fields=["application"], condition=models.Q(is_current=True), name="uniq_current_letter"
            ),
        ]

    def __str__(self):
        return f"Acceptance letter v{self.version} for {self.application_id}"


class ApplicantNote(TimeStampedModel):
    """Private note an organization's team keeps about an applicant. Never shown to the student."""

    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="notes"
    )
    organization = models.ForeignKey(
        "companies.Organization", on_delete=models.CASCADE, related_name="applicant_notes"
    )
    author = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    # Kept so the note still shows who wrote it after that member leaves.
    author_label = models.CharField(max_length=200, blank=True)
    body = models.TextField(max_length=2000)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Note on {self.application_id} by {self.author_label}"


class OrganizationReview(TimeStampedModel):
    """A student's rating of the organization that accepted them (one per accepted application)."""

    application = models.OneToOneField(
        Application, on_delete=models.CASCADE, related_name="review"
    )
    organization = models.ForeignKey(
        "companies.Organization", on_delete=models.CASCADE, related_name="reviews"
    )
    student = models.ForeignKey(
        "students.StudentProfile", on_delete=models.CASCADE, related_name="reviews"
    )
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    comment = models.TextField(blank=True, max_length=1000)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.rating}/5 for {self.organization_id}"