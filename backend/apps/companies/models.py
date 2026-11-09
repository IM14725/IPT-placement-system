from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.students.models import VerificationStatus

MAX_ORGANIZATION_ACCOUNTS = 5
MAX_ORGANIZATION_CONCURRENT_LOGINS = 5


class OrganizationType(models.TextChoices):
    COMPANY = "COMPANY", _("Company")
    UNIVERSITY = "UNIVERSITY", _("University")
    INSTITUTION = "INSTITUTION", _("Institution")


class OrganizationMemberRole(models.TextChoices):
    # The registering user. Shown as "Manager": the first member of an organization is its Manager.
    OWNER = "OWNER", _("Manager (Owner)")
    MANAGER = "MANAGER", _("Deputy Manager")
    RECRUITER = "RECRUITER", _("Organization Recruiter")
    UNIVERSITY_COORDINATOR = "UNIVERSITY_COORDINATOR", _("University Coordinator")
    UNIVERSITY_SUPERVISOR = "UNIVERSITY_SUPERVISOR", _("University Supervisor")


class MembershipStatus(models.TextChoices):
    ACTIVE = "ACTIVE", _("Active")
    INVITED = "INVITED", _("Invited")
    SUSPENDED = "SUSPENDED", _("Suspended")
    # Access revoked by a Manager. The row (and the user account) is kept as history.
    REMOVED = "REMOVED", _("Removed")


class Organization(TimeStampedModel):
    """Unified Organizations table supporting companies, institutions, and future universities."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_organization",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=200, unique=True)
    organization_type = models.CharField(
        max_length=20,
        choices=OrganizationType.choices,
        default=OrganizationType.COMPANY,
    )
    industry = models.CharField(max_length=150, blank=True)
    description = models.TextField(blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    address = models.CharField(max_length=255, blank=True)
    street = models.CharField(max_length=255, blank=True)
    region = models.ForeignKey(
        "locations.Region",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="organizations",
    )
    district = models.ForeignKey(
        "locations.District",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="organizations",
    )
    ward = models.ForeignKey(
        "locations.Ward",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="organizations",
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
        related_name="reviewed_organizations",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspension_reason = models.TextField(blank=True)

    class Meta:
        verbose_name = _("Organization")
        verbose_name_plural = _("Organizations")

    @property
    def is_suspended(self):
        return self.suspended_at is not None

    @property
    def is_approved(self):
        return self.verification_status == VerificationStatus.APPROVED

    @property
    def is_verified(self):
        return self.verification_status == VerificationStatus.APPROVED

    def historical_member_user_ids(self):
        """Every user who has ever belonged to this organization, former members included.

        Organization-owned data (documents, history) must not vanish when a member leaves.
        """
        ids = set(self.members.values_list("user_id", flat=True))
        if self.user_id:
            ids.add(self.user_id)
        return list(ids)

    def member_user_ids(self):
        ids = set(
            self.members.filter(membership_status=MembershipStatus.ACTIVE).values_list(
                "user_id", flat=True
            )
        )
        if self.user_id:
            ids.add(self.user_id)
        return list(ids)

    def save(self, *args, **kwargs):
        if self.address and not self.street:
            self.street = self.address
        elif self.street and not self.address:
            self.address = self.street
        if self.user_id:
            if not self.email and getattr(self.user, "email", ""):
                self.email = self.user.email
            if not self.phone and getattr(self.user, "phone", ""):
                self.phone = self.user.phone
        super().save(*args, **kwargs)
        if self.user_id:
            OrganizationMember.objects.get_or_create(
                organization=self,
                user_id=self.user_id,
                defaults={
                    "role": OrganizationMemberRole.OWNER,
                    "membership_status": MembershipStatus.ACTIVE,
                },
            )

    def __str__(self):
        return self.name


class OrganizationMember(TimeStampedModel):
    """Connects users to organizations, allowing up to 5 accounts to manage one organization."""

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="members",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="organization_memberships",
    )
    role = models.CharField(
        max_length=30,
        choices=OrganizationMemberRole.choices,
        default=OrganizationMemberRole.OWNER,
    )
    membership_status = models.CharField(
        max_length=20,
        choices=MembershipStatus.choices,
        default=MembershipStatus.ACTIVE,
    )
    # Real-world job title (e.g. "Recruitment Officer"); the role above sets platform permissions.
    position = models.CharField(max_length=120, blank=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="member_removals",
    )
    removal_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "user"], name="uniq_organization_member"
            )
        ]

    def clean(self):
        if self.membership_status == MembershipStatus.ACTIVE and self.organization_id:
            active_qs = OrganizationMember.objects.filter(
                organization_id=self.organization_id,
                membership_status=MembershipStatus.ACTIVE,
            )
            if self.pk:
                active_qs = active_qs.exclude(pk=self.pk)
            if active_qs.count() >= MAX_ORGANIZATION_ACCOUNTS:
                raise ValidationError(
                    f"An organization can have at most {MAX_ORGANIZATION_ACCOUNTS} active accounts."
                )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user} @ {self.organization} ({self.role})"


class OrganizationSession(TimeStampedModel):
    """Tracks active concurrent login sessions for an organization (max 5 at once)."""

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="active_sessions",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="organization_sessions",
    )
    session_key = models.CharField(max_length=64, unique=True, db_index=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.organization.name} ({self.user.email}) [{self.session_key[:8]}]"
