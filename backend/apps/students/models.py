from django.utils.translation import gettext_lazy
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
        max_length=40, blank=True, help_text=gettext_lazy("Your university registration number, e.g. 2022-04-01234")
    )
    gender = models.CharField(
        max_length=10,
        choices=Gender.choices,
        blank=True,
        help_text=gettext_lazy("Select your gender."),
    )
    profile_photo = models.ImageField(
        upload_to="profile_photos/",
        blank=True,
        null=True,
        help_text=gettext_lazy("A clear headshot photo (max 2MB)."),
    )
    id_card_photo = models.ImageField(
        upload_to="id_cards/",
        blank=True,
        null=True,
        help_text=gettext_lazy("A clear photo of your student ID card (max 2MB)."),
    )
    university = models.ForeignKey(
        University,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="students",
        help_text=gettext_lazy("Optional. Pick your university, or choose 'not listed' to suggest it."),
    )
    graduation_year = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(2000), MaxValueValidator(2100)],
        help_text=gettext_lazy("Expected year of graduation."),
    )
    course = models.CharField(max_length=200)
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
        help_text=gettext_lazy("Your TCU qualification level (Level 4 Certificate to Level 10 PhD)."),
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

    @property
    def is_complete(self):
        """The profile has what an organization needs to assess the student.

        Applying needs a complete profile and a verified phone; platform-admin approval of
        students was removed (see plan.md, "Dashboard upgrade").
        """
        return bool(self.course and self.region_id and self.district_id)

    def __str__(self):
        return f"{self.user.email} ({self.university or 'no university'})"


def student_can_apply(user):
    """Single rule for "may this student apply": complete profile + verified phone."""
    profile = StudentProfile.objects.filter(user=user).first()
    return bool(profile and profile.is_complete and user.phone_verified)


class SavedSlot(TimeStampedModel):
    """A slot the student bookmarked to come back to."""

    student = models.ForeignKey(
        StudentProfile, on_delete=models.CASCADE, related_name="saved_slots"
    )
    slot = models.ForeignKey(
        "slots.Slot", on_delete=models.CASCADE, related_name="saved_by"
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["student", "slot"], name="uniq_student_saved_slot")
        ]

    def __str__(self):
        return f"{self.student_id} saved {self.slot_id}"


MAX_SAVED_SEARCHES = 10


class SavedSearch(TimeStampedModel):
    """Marketplace filters a student saved ("Notify me").

    When an organization posts or reopens a slot that matches, the student gets an in-app
    notification (apps/students/tasks.py). Empty filters mean "any".
    """

    student = models.ForeignKey(
        StudentProfile, on_delete=models.CASCADE, related_name="saved_searches"
    )
    region = models.ForeignKey(
        "locations.Region", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    district = models.ForeignKey(
        "locations.District", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    department = models.CharField(max_length=200, blank=True)
    organization = models.CharField(max_length=200, blank=True)
    education_level = models.PositiveSmallIntegerField(
        null=True, blank=True, choices=EducationLevel.choices
    )
    academic_year = models.PositiveSmallIntegerField(
        null=True, blank=True, choices=AcademicYear.choices
    )
    is_active = models.BooleanField(default=True)
    last_matched_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def parts(self):
        """Human-readable filter chips, e.g. ["Kinondoni", "Computer Science"]."""
        parts = []
        if self.district_id:
            parts.append(str(self.district))
        elif self.region_id:
            parts.append(str(self.region))
        if self.department:
            parts.append(self.department)
        if self.organization:
            parts.append(self.organization)
        if self.education_level:
            parts.append(self.get_education_level_display())
        if self.academic_year:
            parts.append(self.get_academic_year_display())
        return parts

    @property
    def label(self):
        return " · ".join(self.parts) or "Any new slot"

    def matches(self, slot):
        """Same rules as the marketplace search (apps/slots/views.py)."""
        if self.district_id and slot.district_id != self.district_id:
            return False
        if self.region_id and slot.district.region_id != self.region_id:
            return False
        if self.department and self.department.lower() not in (slot.department or "").lower():
            return False
        if self.organization and self.organization.lower() not in slot.organization.name.lower():
            return False
        if self.education_level and slot.education_level != self.education_level:
            return False
        if self.academic_year and slot.academic_year != self.academic_year:
            return False
        return True

    def query_string(self):
        """The marketplace URL query that reproduces this search."""
        from urllib.parse import urlencode

        params = {}
        if self.region_id:
            params["region"] = str(self.region_id)
        if self.district_id:
            params["district"] = str(self.district_id)
            if not self.region_id:
                params["region"] = str(self.district.region_id)
        if self.department:
            params["department"] = self.department
        if self.organization:
            params["organization"] = self.organization
        if self.education_level:
            params["education_level"] = str(self.education_level)
        if self.academic_year:
            params["academic_year"] = str(self.academic_year)
        return urlencode(params)

    def __str__(self):
        return f"{self.student_id}: {self.label}"