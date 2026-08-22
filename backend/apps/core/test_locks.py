import threading
import time
from unittest import mock

import pytest
from django.test import override_settings

from apps.core.cache import enqueue_once
from apps.core.redis_client import acquire_lock, get_redis, release_lock

PREFIX = "test-lock"


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    client = get_redis()
    for key in client.keys(f"{PREFIX}:*"):
        client.delete(key)


@override_settings(CACHE_KEY_PREFIX=PREFIX)
def test_acquire_release_lock():
    token = acquire_lock("unit:mutex")
    assert token
    assert acquire_lock("unit:mutex") is None  # already held
    release_lock("unit:mutex", token)
    assert acquire_lock("unit:mutex")  # re-acquirable


@override_settings(CACHE_KEY_PREFIX=PREFIX)
def test_release_lock_requires_owner_token():
    token = acquire_lock("unit:mutex")
    assert token
    release_lock("unit:mutex", "wrong-token")  # must not delete another's lock
    assert acquire_lock("unit:mutex") is None
    release_lock("unit:mutex", token)
    assert acquire_lock("unit:mutex")


@override_settings(CACHE_KEY_PREFIX=PREFIX)
def test_blocking_lock_waits_for_release():
    token = acquire_lock("unit:blocking")
    assert token
    result = {}

    def release_soon():
        time.sleep(0.05)
        release_lock("unit:blocking", token)

    t = threading.Thread(target=release_soon)
    t.start()
    got = acquire_lock("unit:blocking", ttl_ms=2000, blocking=True)
    t.join()
    assert got  # acquired after the first holder released


@override_settings(CACHE_KEY_PREFIX=PREFIX)
def test_enqueue_once_dedupes_identical_jobs():
    with mock.patch("celery.current_app") as app:
        assert enqueue_once("test.task", [1]) is True
        assert enqueue_once("test.task", [1]) is False  # duplicate skipped
        assert enqueue_once("test.task", [2]) is True  # different args enqueued
        assert app.send_task.call_count == 2