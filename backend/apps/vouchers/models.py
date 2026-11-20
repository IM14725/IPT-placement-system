"""Fee vouchers: admin-issued discount codes for the application fee.

A ``VoucherBatch`` holds the terms (discount, validity, budget, who may use it); each
``FeeVoucher`` is one redeemable code in that batch. Using a code on an application writes one
``VoucherRedemption`` (the fee, the discount and what the student actually pays).

Nothing here keeps a "used" counter. A redemption counts against its code's usage cap and the
batch budget only while its application is PENDING or PAID, exactly like a slot seat. When the
application expires unpaid, is released or is cancelled, the use is free again with no write
(so the FastAPI payment webhook never has to know about vouchers).
"""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext, gettext_lazy as _

from apps.core.models import TimeStampedModel


class DiscountType(models.TextChoices):
    PERCENT = "PERCENT", _("Percentage off")
    FIXED = "FIXED", _("Fixed amount off (TZS)")
    WAIVER = "WAIVER", _("Full waiver (no payment)")


class CodeMode(models.TextChoices):
    UNIQUE = "UNIQUE", _("Unique single-use codes")
    SHARED = "SHARED", _("One shared campaign code")


# Redemptions of applications in these states hold a use of the code (the seat is held or paid).
ACTIVE_APPLICATION_STATUSES = ("PENDING", "PAID")


class VoucherBatch(TimeStampedModel):
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    discount_type = models.CharField(max_length=10, choices=DiscountType.choices, default=DiscountType.PERCENT)
    # Percentage (1-100) or TZS amount. Ignored for a full waiver.
    discount_value = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    code_mode = models.CharField(max_length=10, choices=CodeMode.choices, default=CodeMode.UNIQUE)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="voucher_batches"
    )
    issuer_label = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    # How many times one student may use codes from this batch (a student can still not reuse one
    # code on the same application).
    per_student_limit = models.PositiveSmallIntegerField(default=1)
    # Cap on the total discount given by this batch (TZS). Empty means no cap.
    budget_tzs = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)

    # ---- Who may use it. An empty restriction means "no limit"; every set one must match.
    universities = models.ManyToManyField("students.University", blank=True, related_name="voucher_batches")
    courses = models.JSONField(default=list, blank=True)  # student's course, matched ignoring case
    regions = models.ManyToManyField("locations.Region", blank=True, related_name="voucher_batches")  # student's region
    districts = models.ManyToManyField(
        "locations.District", blank=True, related_name="voucher_batches"
    )  # student's district
    signup_from = models.DateTimeField(null=True, blank=True)  # account created on/after
    signup_to = models.DateTimeField(null=True, blank=True)  # account created on/before
    allowed_emails = models.JSONField(default=list, blank=True)  # named students (sponsored cohort)

    # ---- Which slots it applies to.
    organizations = models.ManyToManyField("companies.Organization", blank=True, related_name="voucher_batches")
    slots = models.ManyToManyField("slots.Slot", blank=True, related_name="voucher_batches")
    slot_districts = models.ManyToManyField(
        "locations.District", blank=True, related_name="slot_voucher_batches"
    )
    slot_start_from = models.DateField(null=True, blank=True)  # slot starts on/after
    slot_start_to = models.DateField(null=True, blank=True)  # slot starts on/before

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "voucher batches"

    def __str__(self):
        return self.name

    @property
    def is_waiver(self):
        return self.discount_type == DiscountType.WAIVER

    @property
    def discount_label(self):
        if self.discount_type == DiscountType.WAIVER:
            return gettext("100% waiver")
        if self.discount_type == DiscountType.PERCENT:
            return gettext("%(percent)s%% off") % {"percent": f"{Decimal(self.discount_value).normalize():f}"}
        return gettext("TZS %(amount)s off") % {"amount": f"{self.discount_value:,.0f}"}


class FeeVoucher(TimeStampedModel):
    batch = models.ForeignKey(VoucherBatch, on_delete=models.CASCADE, related_name="codes")
    # Stored normalized: upper case letters and digits only (see services.normalize_code).
    code = models.CharField(max_length=40, unique=True)
    max_uses = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)  # False once an admin revokes the code

    class Meta:
        ordering = ["created_at", "code"]

    def __str__(self):
        return self.display_code

    @property
    def display_code(self):
        # Generated codes are IPT + 8 characters, shown as IPT-XXXX-XXXX so they are easy to read out.
        if len(self.code) == 11 and self.code.startswith("IPT"):
            return f"IPT-{self.code[3:7]}-{self.code[7:]}"
        return self.code


class VoucherRedemption(TimeStampedModel):
    """One voucher used on one application. Append-only once the application is paid (DB trigger)."""

    voucher = models.ForeignKey(FeeVoucher, on_delete=models.PROTECT, related_name="redemptions")
    batch = models.ForeignKey(VoucherBatch, on_delete=models.PROTECT, related_name="redemptions")
    application = models.OneToOneField(
        "applications.Application", on_delete=models.CASCADE, related_name="voucher_redemption"
    )
    student = models.ForeignKey(
        "students.StudentProfile", on_delete=models.CASCADE, related_name="voucher_redemptions"
    )
    fee_amount = models.DecimalField(max_digits=12, decimal_places=2)  # the full application fee
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2)
    amount_charged = models.DecimalField(max_digits=12, decimal_places=2)  # what the student pays
    from_signup = models.BooleanField(default=False)  # applied from the code given at registration

    class Meta:
        ordering = ["-created_at"]
        # voucher and batch are ForeignKeys, which Postgres/Django already index; no extra indexes needed.

    def __str__(self):
        return f"{self.voucher} on {self.application_id}"


class SignupReference(TimeStampedModel):
    """The voucher or sponsor code a student gave when registering.

    Registration never fails because of it. If the code was fine it is SAVED and applied to the
    student's first application automatically; otherwise it is REJECTED with the reason so an admin
    can see what was typed.
    """

    class Status(models.TextChoices):
        SAVED = "SAVED", _("Saved")
        REJECTED = "REJECTED", _("Rejected")

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="signup_reference")
    raw_code = models.CharField(max_length=60)
    voucher = models.ForeignKey(FeeVoucher, null=True, blank=True, on_delete=models.SET_NULL, related_name="signup_references")
    status = models.CharField(max_length=10, choices=Status.choices)
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user_id}: {self.raw_code} ({self.status})"
