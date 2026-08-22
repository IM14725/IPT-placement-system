import time

import pytest
from django.test import override_settings

from apps.core.rate_limit import consume
from apps.core.redis_client import get_redis

PREFIX = "test-rl"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    client = get_redis()
    for key in client.keys(f"{PREFIX}:rl:*"):
        client.delete(key)


@override_settings(CACHE_KEY_PREFIX=PREFIX)
def test_token_bucket_allows_up_to_capacity():
    for _ in range(5):
        res = consume("unit", "bucket", capacity=5, refill_per_second=1.0, now=1000)
        assert res.allowed
    res = consume("unit", "bucket", capacity=5, refill_per_second=1.0, now=1000)
    assert not res.allowed
    assert res.retry_after > 0


@override_settings(CACHE_KEY_PREFIX=PREFIX)
def test_token_bucket_refills_over_time():
    now = 1_000_000.0  # seconds
    for _ in range(5):
        consume("unit", "bucket", capacity=5, refill_per_second=2.0, now=now)
    # Empty after 5 draws; refill rate 2/sec -> only 0.5 tokens after 0.25s.
    res = consume("unit", "bucket", capacity=5, refill_per_second=2.0, now=now + 0.25)
    assert not res.allowed
    res = consume("unit", "bucket", capacity=5, refill_per_second=2.0, now=now + 1.0)
    assert res.allowed
    assert res.remaining >= 1.0


@override_settings(CACHE_KEY_PREFIX=PREFIX)
def test_token_bucket_is_per_bucket():
    consume("unit", "bucket-a", capacity=1, refill_per_second=1.0, now=1000)
    assert consume("unit", "bucket-a", capacity=1, refill_per_second=1.0, now=1000).allowed is False
    # Different bucket is unaffected.
    assert consume("unit", "bucket-b", capacity=1, refill_per_second=1.0, now=1000).allowed is True


@override_settings(CACHE_KEY_PREFIX=PREFIX)
def test_token_bucket_idle_expiry_resets():
    now = int(time.time() * 1000)
    consume("unit", "bucket", capacity=2, refill_per_second=0.01, now=now)
    # TTL covers the refill-to-full window; after it the key is gone and a
    # fresh bucket starts full again.
    client = get_redis()
    key = f"{PREFIX}:rl:unit:bucket"
    client.delete(key)
    res = consume("unit", "bucket", capacity=2, refill_per_second=0.01, now=now)
    assert res.allowed
    assert res.remaining == 1.0