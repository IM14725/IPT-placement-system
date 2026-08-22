from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class NotificationType(models.TextChoices):
    APPLICATION = "APPLICATION", "Application"
    ACCEPTANCE = "ACCEPTANCE", "Acceptance"


class Notification(TimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    type = models.CharField(max_length=20, choices=NotificationType.choices)
    title = models.CharField(max_length=200)
    body = models.TextField()
    link = models.CharField(max_length=255, blank=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_type_display()}: {self.title}"


class MessageChannel(models.TextChoices):
    SMS = "SMS", "SMS"
    EMAIL = "EMAIL", "Email"


class MessageStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    SENT = "SENT", "Sent"
    FAILED = "FAILED", "Failed"


class Message(TimeStampedModel):
    application = models.ForeignKey(
        "applications.Application",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="messages",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="outbound_messages",
    )
    channel = models.CharField(max_length=10, choices=MessageChannel.choices)
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField()
    status = models.CharField(
        max_length=10, choices=MessageStatus.choices, default=MessageStatus.PENDING
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_channel_display()} to {self.user_id} ({self.status})"


class NotificationTemplate(models.Model):
    """Editable Email/SMS message templates used by the automated senders."""

    key = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=200)
    channel = models.CharField(max_length=10, choices=MessageChannel.choices)
    trigger_label = models.CharField(max_length=200, blank=True)
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField(blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return self.name