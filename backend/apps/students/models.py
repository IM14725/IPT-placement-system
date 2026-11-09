from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from apps.core.education import EducationLevel, AcademicYear
from apps.core.models import TimeStampedModel


class VerificationStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    APPROVED = "APPROVED", _("Approved")
    REJECTED = "REJECTED", _("Rejected")


class Gender(models.TextChoices):
    MALE = "MALE", _("Male")
    FEMALE = "FEMALE", _("Female")


class InstitutionType(models.TextChoices):
    UNIVERSITY = "UNIVERSITY", _("University")
    COLLEGE = "COLLEGE", _("College")
    INSTITUTE = "INSTITUTE", _("Institute")


class University(TimeStampedModel):
    """Centralized record of a higher-education institution.

    Students reference one of these for their affiliation (it is optional). A student
    whose university is missing submits a suggestion, which is stored here as PENDING
    until a platform admin approves or rejects it. Affiliation alone grants no access.
    """

    name = models.CharField(max_length=200)
    abbreviation = models.CharField(max_length=20, blank=True)
    institution_type = models.CharField(
        max_length=12, choices=InstitutionType.choices, default=InstitutionType.UNIVERSITY
    )
    region = models.ForeignKey(
        "locations.Region",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="universities",
    )
    website = models.URLField(blank=True)
    verification_status = models.CharField(
        max_length=10,
        choices=VerificationStatus.choices,
        default=VerificationStatus.APPROVED,
    )
    suggested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="suggested_universities",
    )

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "universities"
        constraints = [
            models.UniqueConstraint(Lower("name"), name="uniq_university_name_ci"),
        ]

    @property
    def is_approved(self):
        return self.verification_status == VerificationStatus.APPROVED

    def __str__(self):
        return self.name


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
    university = models.ForeignKey(
        University,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="students",
        help_text="Optional. Pick your university, or choose 'not listed' to suggest it.",
    )
    graduation_year = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(2000), MaxValueValidator(2100)],
        help_text="Expected year of graduation.",
    )
    course = models.CharField(max_length=200)
    academic_year = models.PositiveSmallIntegerField(
        null=True, 
        blank=True,
        choices=AcademicYear.choices,
        help_text="Required if Higher Diploma is selected"
    )
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
    reapplication_credit = models.BooleanField(default=False)
    verification_status = models.CharField(
        max_length=10,
        choices=VerificationStatus.choices,
        default=VerificationStatus.APPROVED,
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
        return self.academic_year

    def __str__(self):
        return f"{self.user.email} ({self.university or 'no university'})"