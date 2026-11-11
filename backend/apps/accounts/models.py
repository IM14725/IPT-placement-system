import uuid
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone


class UserRole(models.TextChoices):
    STUDENT = "STUDENT", "Student"
    ORGANIZATION = "ORGANIZATION", "Organization"
    ADMIN = "ADMIN", "Platform Admin"
    SUPER_ADMIN = "SUPER_ADMIN", "Platform Super Admin"


# Console modules/capabilities an AdminRole can grant (see core.views.role_management).
ADMIN_MODULES = ("user_management", "slot_verification", "financial_ledger", "system_metrics", "locations")
ADMIN_CAPS = ("view", "create", "edit", "delete")


class AdminRole(models.Model):
    """Administrative role with a capability matrix for the admin console."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=255, blank=True)
    permissions = models.JSONField(default=dict, blank=True)
    privileges = models.JSONField(default=dict, blank=True)
    is_system = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class UserManager(BaseUserManager):
    """Custom manager using email as the unique identifier."""

    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", UserRole.SUPER_ADMIN)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = None
    email = models.EmailField("email address", unique=True)
    role = models.CharField(
        max_length=20, choices=UserRole.choices, default=UserRole.STUDENT
    )
    # Capability matrix for platform admins; Super Admins ignore it.
    admin_role = models.ForeignKey(
        AdminRole,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="users",
    )
    phone = models.CharField(max_length=20, blank=True)
    email_verified = models.BooleanField(default=False)
    phone_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    promo_sms_optout = models.BooleanField(default=False)
    deletion_requested_at = models.DateTimeField(null=True, blank=True)
    # Set when a platform admin suspends the account (is_active is also cleared).
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspension_reason = models.TextField(blank=True)
    # The one live Django session for this account (one active device per user).
    current_session_key = models.CharField(max_length=40, blank=True, default="")
    # Admin onboarding: a temporary password must be replaced before anything else.
    must_change_password = models.BooleanField(default=False)
    temp_password_expires_at = models.DateTimeField(null=True, blank=True)
    job_title = models.CharField(max_length=120, blank=True)
    # Designated to approve Super Admin recovery requests (M-of-N quorum).
    is_recovery_approver = models.BooleanField(default=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta(AbstractUser.Meta):
        constraints = [
            # One account per phone number (blank = no phone yet).
            models.UniqueConstraint(
                fields=["phone"],
                condition=~models.Q(phone=""),
                name="uniq_user_phone_nonblank",
            )
        ]

    @property
    def is_student(self):
        return self.role == UserRole.STUDENT

    @property
    def full_name(self):
        name = (self.first_name or "").strip()
        surname = (self.last_name or "").strip()
        if name and surname:
            return f"{name} {surname}"
        if name or surname:
            return name or surname
        if self.is_organization:
            org = self.organization
            if org and org.name:
                return org.name
        return self.email

    @property
    def organization(self):
        try:
            if hasattr(self, "owned_organization") and self.owned_organization is not None:
                return self.owned_organization
        except Exception:
            pass
        membership = (
            self.organization_memberships.filter(membership_status="ACTIVE")
            .select_related("organization")
            .first()
        )
        return membership.organization if membership else None

    @property
    def is_organization(self):
        return self.role == UserRole.ORGANIZATION

    @property
    def is_super_admin(self):
        return self.is_active and (self.role == UserRole.SUPER_ADMIN or self.is_superuser)

    @property
    def is_platform_admin(self):
        """Any platform-level admin (Platform Admin or Super Admin)."""
        return self.is_active and self.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN)

    def has_admin_permission(self, module, cap="view"):
        """Platform console check: Super Admin always; Platform Admin via AdminRole matrix."""
        if not self.is_active:
            return False
        if self.is_super_admin:
            return True
        if self.role != UserRole.ADMIN or self.admin_role_id is None:
            return False
        return bool((self.admin_role.permissions or {}).get(module, {}).get(cap))

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()
        # Digits only, so "+255712345678" and "255712345678" are the same number.
        self.phone = normalize_phone(self.phone)
        super().save(*args, **kwargs)


def normalize_phone(value):
    return "".join(ch for ch in (value or "") if ch.isdigit())


class OtpPurpose(models.TextChoices):
    REGISTER = "REGISTER", "Registration"
    RECOVERY = "RECOVERY", "Password recovery"
    CHANGE_EMAIL = "CHANGE_EMAIL", "Change email"
    CHANGE_PHONE = "CHANGE_PHONE", "Change phone"


class OtpChannel(models.TextChoices):
    EMAIL = "EMAIL", "Email"
    SMS = "SMS", "SMS"


class OneTimeCode(models.Model):
    """A hashed, single-use, expiring verification code. The plain code is never stored."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="one_time_codes")
    purpose = models.CharField(max_length=20, choices=OtpPurpose.choices)
    channel = models.CharField(max_length=5, choices=OtpChannel.choices)
    destination = models.CharField(max_length=254)
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["user", "purpose", "-created_at"])]

# ── Administrator MFA, recovery and break-glass ─────────────────────────────

class TotpDevice(models.Model):
    """Authenticator-app secret (encrypted at rest). ``last_counter`` blocks code replay."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="totp_device")
    secret_enc = models.TextField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_counter = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)


class WebAuthnCredential(models.Model):
    """A FIDO2 / WebAuthn security key."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="webauthn_credentials")
    credential_id = models.CharField(max_length=512, unique=True)  # base64url
    public_key = models.BinaryField()
    sign_count = models.BigIntegerField(default=0)
    name = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]


class RecoveryCode(models.Model):
    """One-use offline MFA recovery code. Only a keyed hash is stored."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="recovery_codes")
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "code_hash"])]


class RecoveryStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    EXECUTED = "EXECUTED", "Approved and executed"
    REJECTED = "REJECTED", "Rejected"
    EXPIRED = "EXPIRED", "Expired"


class AdminRecoveryRequest(models.Model):
    """M-of-N recovery of an administrator's MFA, approved by designated administrators."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    target = models.ForeignKey(User, on_delete=models.CASCADE, related_name="recovery_requests")
    status = models.CharField(max_length=10, choices=RecoveryStatus.choices, default=RecoveryStatus.PENDING)
    required_approvals = models.PositiveSmallIntegerField(default=2)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    executed_at = models.DateTimeField(null=True, blank=True)
    requester_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class AdminRecoveryApproval(models.Model):
    request = models.ForeignKey(AdminRecoveryRequest, on_delete=models.CASCADE, related_name="decisions")
    approver = models.ForeignKey(User, on_delete=models.CASCADE, related_name="recovery_decisions")
    approve = models.BooleanField()
    note = models.CharField(max_length=200, blank=True)
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["request", "approver"], name="uniq_recovery_decision")]


class BreakGlassAccount(models.Model):
    """Emergency Super Admin: offline credentials + hardware key, never for routine use."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="break_glass")
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="break_glass_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    rotation_required = models.BooleanField(default=False)
    review_pending = models.BooleanField(default=False)
