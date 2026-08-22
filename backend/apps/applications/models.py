from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.documents.validators import validate_upload


def application_letter_upload_path(instance, filename):
    return f"application_letters/{instance.student_id}/{filename}"


class ApplicationStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    PAID = "PAID", _("Paid")
    UNPAID = "UNPAID", _("Unpaid")


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

    def accept(self):
        self.is_accepted = True
        self.save(update_fields=["is_accepted", "updated_at"])

    def __str__(self):
        return f"Application #{self.pk} - {self.student} -> {self.slot}"