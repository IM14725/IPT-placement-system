from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class LedgerSnapshot(TimeStampedModel):
    data = models.JSONField(default=dict)

    def __str__(self):
        return f"Ledger snapshot at {self.created_at:%Y-%m-%d %H:%M}"


class AuditLog(models.Model):
    """Immutable record of administrative / system activity."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )
    actor_label = models.CharField(max_length=200, blank=True)
    action = models.CharField(max_length=120)
    module = models.CharField(max_length=80)
    description = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    is_system = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} ({self.module}) @ {self.created_at:%Y-%m-%d %H:%M}"


class IntegrityRecord(models.Model):
    """Append-only, tamper-evident ledger of sealed critical records."""

    record_type = models.CharField(max_length=30, db_index=True)
    record_id = models.PositiveBigIntegerField()
    record_hash = models.CharField(max_length=64)
    prev_hash = models.CharField(max_length=64, default="", blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["record_type", "record_id"])]

    def __str__(self):
        return f"{self.record_type}#{self.record_id} {self.record_hash[:12]}…"


class PlatformSetting(models.Model):
    """Key/value platform configuration editable from the admin console."""

    key = models.CharField(max_length=80, unique=True)
    label = models.CharField(max_length=200)
    value_type = models.CharField(
        max_length=20,
        choices=[
            ("text", "Text"),
            ("number", "Number"),
            ("bool", "Boolean"),
            ("list", "List"),
        ],
        default="text",
    )
    value_text = models.TextField(blank=True)
    value_number = models.FloatField(null=True, blank=True)
    value_bool = models.BooleanField(default=False)
    value_list = models.JSONField(default=list, blank=True)
    is_secret = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]

    def get_value(self):
        if self.value_type == "number":
            return self.value_number
        if self.value_type == "bool":
            return self.value_bool
        if self.value_type == "list":
            return self.value_list
        return self.value_text

    def __str__(self):
        return self.key