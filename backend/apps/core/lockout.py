"""Temporary lockout after repeated failures (Redis counters, fail open).

Complements ``rate_limit`` (which throttles request rate): this counts *failed
credentials* for one identifier and blocks it for a fixed period. It never disables an
account, and it keys on the submitted identifier whether or not an account exists, so the
response does not reveal which emails are registered.
"""

from django.conf import settings

from apps.core.redis_client import get_redis


def _key(scope, ident, kind):
    return f"{settings.CACHE_KEY_PREFIX}:lockout:{scope}:{ident}:{kind}"


def lock_remaining(scope, ident):
    """Seconds left on an active lock, or 0."""
    try:
        ttl = get_redis().ttl(_key(scope, ident, "lock"))
    except Exception:  # noqa: BLE001
        return 0
    return ttl if ttl and ttl > 0 else 0


def register_failure(scope, ident, max_attempts, lock_seconds):
    """Count a failure; once ``max_attempts`` is reached, lock for ``lock_seconds``."""
    try:
        r = get_redis()
        fails_key = _key(scope, ident, "fails")
        fails = r.incr(fails_key)
        r.expire(fails_key, lock_seconds * 3)
        if fails >= max_attempts:
            r.set(_key(scope, ident, "lock"), 1, ex=lock_seconds)
            r.delete(fails_key)
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def clear(scope, ident):
    try:
        get_redis().delete(_key(scope, ident, "fails"))
    except Exception:  # noqa: BLE001
        pass
