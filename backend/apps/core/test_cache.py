import pytest
from django.test import override_settings

from apps.core.cache import (
    bump_slot_version,
    cache_delete,
    cache_get,
    cache_get_or_set,
    cache_set,
    get_slot_version,
)
from apps.core.redis_client import get_redis

PREFIX = "test-cache"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    client = get_redis()
    for key in client.keys(f"{PREFIX}:*"):
        client.delete(key)


@override_settings(CACHE_ENABLED=True, CACHE_KEY_PREFIX=PREFIX)
def test_cache_get_or_set_returns_cached_value():
    calls = []

    def producer():
        calls.append(1)
        return {"slots": [1, 2, 3]}

    first = cache_get_or_set("unit:value", 60, producer)
    second = cache_get_or_set("unit:value", 60, producer)
    assert first == {"slots": [1, 2, 3]}
    assert second == first
    assert len(calls) == 1
    assert cache_get("unit:value") == first


@override_settings(CACHE_ENABLED=True, CACHE_KEY_PREFIX=PREFIX)
def test_cache_get_or_set_recomputes_after_expiry():
    calls = []

    def producer():
        calls.append(1)
        return "x"

    cache_get_or_set("unit:ttl", 1, producer)
    assert len(calls) == 1
    # Force expiry + eviction so the next call recomputes.
    get_redis().delete(f"{PREFIX}:unit:ttl")
    cache_get_or_set("unit:ttl", 1, producer)
    assert len(calls) == 2


@override_settings(CACHE_ENABLED=True, CACHE_KEY_PREFIX=PREFIX)
def test_slot_version_bump_invalidates():
    before = get_slot_version()
    bump_slot_version()
    assert get_slot_version() == before + 1


@override_settings(CACHE_ENABLED=True, CACHE_KEY_PREFIX=PREFIX)
def test_cache_set_serializes_decimal_and_datetime():
    import datetime
    import decimal

    cache_set("unit:types", {"amount": decimal.Decimal("15000.00"), "at": datetime.datetime(2026, 8, 16, 10, 0)})
    cached = cache_get("unit:types")
    assert cached["amount"] == 15000.0
    assert cached["at"] == "2026-08-16T10:00:00"
    cache_delete("unit:types")
    assert cache_get("unit:types") is None