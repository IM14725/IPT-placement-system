import json
import time
import uuid

import redis
from django.conf import settings

_pool = None


def get_redis():
    global _pool
    if _pool is None:
        _pool = redis.ConnectionPool.from_url(settings.REDIS_URL)
    return redis.Redis(connection_pool=_pool)


def publish(channel: str, payload: dict):
    get_redis().publish(channel, json.dumps(payload, default=str))


def acquire_lock(name: str, ttl_ms: int = 30000, blocking: bool = False) -> str | None:
    """Distributed mutex (SET NX PX). Returns the lock token or None.

    Used to serialize race-prone sections (e.g. register-by-email) across
    multiple workers/processes without touching the database.
    """
    token = uuid.uuid4().hex
    client = get_redis()
    key = f"{settings.CACHE_KEY_PREFIX}:lock:{name}"
    deadline = None
    if blocking:
        deadline = time.monotonic() + (ttl_ms / 1000)
    while True:
        try:
            if client.set(key, token, nx=True, px=ttl_ms):
                return token
        except Exception:  # noqa: BLE001 - fail open
            return None
        if not blocking or time.monotonic() >= deadline:
            return None
        time.sleep(0.02)


def release_lock(name: str, token: str):
    """Release the lock only if we still own it (compare-and-delete)."""
    client = get_redis()
    key = f"{settings.CACHE_KEY_PREFIX}:lock:{name}"
    try:
        client.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end",
            1,
            key,
            token,
        )
    except Exception:  # noqa: BLE001 - fail open
        pass