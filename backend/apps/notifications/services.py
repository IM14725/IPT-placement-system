from apps.notifications.models import Message, MessageChannel, MessageStatus
from apps.core.rate_limit import consume


class OutboundRateLimited(Exception):
    """Raised when the global outbound send budget for a channel is exhausted."""

    def __init__(self, channel, retry_after):
        self.channel = channel
        self.retry_after = retry_after
        super().__init__(f"{channel} outbound budget exhausted (retry in {retry_after}s)")


# Global outbound budget so bursts of payment/receipt/acceptance emails and SMS
# never hammer the gateway or time out the worker: senders draw from a shared
# Redis token bucket and the Celery task retries after the refill delay.
OUTBOUND_BUDGET = {
    MessageChannel.SMS: {"capacity": 20, "refill_per_second": 5.0},
    MessageChannel.EMAIL: {"capacity": 30, "refill_per_second": 10.0},
}


def create_message(*, user, application=None, channel, subject="", body="") -> Message:
    return Message.objects.create(
        user=user,
        application=application,
        channel=channel,
        subject=subject,
        body=body,
    )


def _safe_format(text, context):
    if not text:
        return text
    try:
        return text.format(**context)
    except (KeyError, IndexError, ValueError):
        return text


def render_template(key, channel, context=None, *, fallback_subject="", fallback_body=""):
    """Render an editable NotificationTemplate if present, else the fallback text."""
    from apps.notifications.models import NotificationTemplate

    template = (
        NotificationTemplate.objects.filter(key=key, channel=channel, is_active=True)
        .order_by("-updated_at")
        .first()
    )
    context = context or {}
    if template is not None:
        subject = _safe_format(template.subject, context) or fallback_subject
        body = _safe_format(template.body, context) or fallback_body
        return subject, body
    return fallback_subject, fallback_body


def dispatch_message(message: Message) -> Message:
    """Send via the appropriate provider and update the delivery status."""
    from apps.core import providers

    message.attempts += 1
    try:
        if message.channel == MessageChannel.SMS:
            providers.send_sms(message.user.phone, message.body)
        else:
            providers.send_email(
                subject=message.subject or "IPT Marketplace",
                to=[message.user.email],
                body=message.body,
            )
        message.status = MessageStatus.SENT
        message.error = ""
    except Exception as exc:  # noqa: BLE001
        message.status = MessageStatus.FAILED
        message.error = str(exc)
    message.save(
        update_fields=["attempts", "status", "error", "updated_at"]
    )
    return message


def throttled_dispatch(message: Message) -> Message:
    """Dispatch a message under the global outbound budget.

    Draws a token from the channel's Redis token bucket first; when the budget
    is exhausted it raises :class:`OutboundRateLimited` so the Celery task can
    retry after ``retry_after`` seconds instead of failing or queueing
    unbounded work.
    """
    budget = OUTBOUND_BUDGET.get(message.channel)
    if budget:
        result = consume(
            "outbound",
            message.channel.lower(),
            budget["capacity"],
            budget["refill_per_second"],
        )
        if not result.allowed:
            raise OutboundRateLimited(message.channel, result.retry_after)
    return dispatch_message(message)