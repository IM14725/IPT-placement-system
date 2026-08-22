from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel
from apps.students.models import VerificationStatus


class CompanyProfile(TimeStampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="company_profile",
    )
    name = models.CharField(max_length=200, unique=True)
    industry = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    street = models.CharField(max_length=255, blank=True)
    region = models.ForeignKey(
        "locations.Region",
        null=True,
        on_delete=models.SET_NULL,
        related_name="companies",
    )
    district = models.ForeignKey(
        "locations.District",
        null=True,
        on_delete=models.SET_NULL,
        related_name="companies",
    )
    ward = models.ForeignKey(
        "locations.Ward",
        null=True,
        on_delete=models.SET_NULL,
        related_name="companies",
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
        related_name="reviewed_companies",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_approved(self):
        return self.verification_status == VerificationStatus.APPROVED

    def __str__(self):
        return self.name