from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.education import EducationLevel
from apps.core.models import TimeStampedModel


class VerificationStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    APPROVED = "APPROVED", _("Approved")
    REJECTED = "REJECTED", _("Rejected")


class Gender(models.TextChoices):
    MALE = "MALE", _("Male")
    FEMALE = "FEMALE", _("Female")


class StudentProfile(TimeStampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="student_profile",
    )
    student_id = models.CharField(
        max_length=40, blank=True, help_text="Your university registration number, e.g. 2022-04-01234"
    )
    gender = models.CharField(
        max_length=10,
        choices=Gender.choices,
        blank=True,
        help_text="Select your gender.",
    )
    profile_photo = models.ImageField(
        upload_to="profile_photos/",
        blank=True,
        null=True,
        help_text="A clear headshot photo (max 2MB).",
    )
    id_card_photo = models.ImageField(
        upload_to="id_cards/",
        blank=True,
        null=True,
        help_text="A clear photo of your student ID card (max 2MB).",
    )
    university = models.CharField(max_length=200)
    course = models.CharField(max_length=200)
    current_year = models.PositiveSmallIntegerField()
    education_level = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        choices=EducationLevel.choices,
        help_text="Your TCU qualification level (Level 4 Certificate to Level 10 PhD).",
    )
    gpa = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True)
    skills = models.JSONField(default=list, blank=True)
    region = models.ForeignKey(
        "locations.Region",
        null=True,
        on_delete=models.SET_NULL,
        related_name="students",
    )
    district = models.ForeignKey(
        "locations.District",
        null=True,
        on_delete=models.SET_NULL,
        related_name="students",
    )
    ward = models.ForeignKey(
        "locations.Ward",
        null=True,
        on_delete=models.SET_NULL,
        related_name="students",
    )
    verification_status = models.CharField(
        max_length=10,
        choices=VerificationStatus.choices,
        default=VerificationStatus.PENDING,
    )
    rejection_reason = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_students",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_verified(self):
        return self.verification_status == VerificationStatus.APPROVED

    @property
    def level(self):
        return self.current_year

    def __str__(self):
        return f"{self.user.email} ({self.university})"