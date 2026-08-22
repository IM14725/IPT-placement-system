import uuid

from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


class PaymentMethod(models.TextChoices):
    M_PESA = "M_PESA", "M-Pesa"
    TIGO_PESA = "TIGO_PESA", "Tigo Pesa"
    AIRTEL_MONEY = "AIRTEL_MONEY", "Airtel Money"


class PaymentGateway(models.TextChoices):
    MOCK = "MOCK", "Mock Simulator"
    SELCOM = "SELCOM", "Selcom"
    PESAPAL = "PESAPAL", "Pesapal"


class PaymentStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    PAID = "PAID", "Paid"
    FAILED = "FAILED", "Failed"


def generate_reference_id() -> str:
    return f"IPT-{uuid.uuid4().hex[:10].upper()}"


class Payment(TimeStampedModel):
    reference_id = models.CharField(max_length=40, unique=True, default=generate_reference_id)
    student = models.ForeignKey(
        "students.StudentProfile",
        on_delete=models.CASCADE,
        related_name="payments",
    )
    application = models.OneToOneField(
        "applications.Application",
        on_delete=models.CASCADE,
        related_name="payment",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="TZS")
    method = models.CharField(max_length=15, choices=PaymentMethod.choices)
    gateway = models.CharField(
        max_length=15, choices=PaymentGateway.choices, default=PaymentGateway.MOCK
    )
    gateway_txn_id = models.CharField(max_length=100, blank=True)
    callback_payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=10, choices=PaymentStatus.choices, default=PaymentStatus.PENDING
    )
    is_paid = models.BooleanField(default=False)
    paid_at = models.DateTimeField(null=True, blank=True)
    receipt_pdf = models.FileField(upload_to="receipts/", null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["reference_id"]),
            models.Index(fields=["status"]),
        ]

    def mark_paid(self, gateway_txn_id="", payload=None):
        self.status = PaymentStatus.PAID
        self.is_paid = True
        self.paid_at = timezone.now()
        if gateway_txn_id:
            self.gateway_txn_id = gateway_txn_id
        if payload is not None:
            self.callback_payload = payload
        self.save(update_fields=[
            "status", "is_paid", "paid_at", "gateway_txn_id", "callback_payload",
            "updated_at",
        ])

    def __str__(self):
        return self.reference_id