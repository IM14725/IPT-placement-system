import time
from unittest import mock

import pytest
from django.test import override_settings

from apps.accounts.models import User
from apps.core.rate_limit import consume
from apps.core.redis_client import get_redis
from apps.notifications.models import Message, MessageChannel, MessageStatus
from apps.notifications.services import OutboundRateLimited, throttled_dispatch

PREFIX = "test-outbound"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    client = get_redis()
    for key in client.keys(f"{PREFIX}:rl:*"):
        client.delete(key)


@pytest.mark.django_db
@override_settings(CACHE_KEY_PREFIX=PREFIX)
def test_throttled_dispatch_raises_when_budget_exhausted():
    user = User.objects.create_user(email="student@x.tz", password="Secret123!")
    msg = Message.objects.create(user=user, channel=MessageChannel.SMS, body="hi")
    # Drain the shared SMS budget (capacity 20, refill 5/s) at current time.
    now = int(time.time() * 1000)
    for _ in range(20):
        consume("outbound", "sms", capacity=20, refill_per_second=5.0, now=now)
    with pytest.raises(OutboundRateLimited) as excinfo:
        throttled_dispatch(msg)
    assert excinfo.value.channel == MessageChannel.SMS
    assert excinfo.value.retry_after > 0
    msg.refresh_from_db()
    assert msg.status == MessageStatus.PENDING  # never reached the provider
    assert msg.attempts == 0


@pytest.mark.django_db
@override_settings(CACHE_KEY_PREFIX=PREFIX)
def test_throttled_dispatch_dispatches_when_budget_allows():
    user = User.objects.create_user(email="student@x.tz", password="Secret123!")
    msg = Message.objects.create(user=user, channel=MessageChannel.SMS, body="hi")
    with mock.patch(
        "apps.notifications.services.dispatch_message", return_value=msg
    ) as dispatch:
        result = throttled_dispatch(msg)
        assert result is msg
        dispatch.assert_called_once_with(msg)