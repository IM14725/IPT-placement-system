"""Redis-backed hot-key cache with cache-stampede protection.

Reduces database hits when many students hit the same slot simultaneously:
the first request computes a value and the rest reuse it until the TTL expires
(singleflight via a short-lived Redis lock). Versioned slot-search keys make
invalidation cheap (a single INCR) and work from both Django and the FastAPI
webhook, which writes slot/applications via raw SQL (bypassing Django signals).
"""

import datetime
import decimal
import hashlib
import json
import time

from django.conf import settings

from apps.core.redis_client import get_redis

_LOCK_TTL_MS = 3000
_POLL_INTERVAL_MS = 0.005
_POLL_MAX_MS = 2500

_SLOT_VERSION_KEY = "slot:cache:version"


def _enabled():
    return getattr(settings, "CACHE_ENABLED", True)


def _key(name):
    return f"{settings.CACHE_KEY_PREFIX}:{name}"


def _json_default(value):
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    return str(value)


def _dump(value):
    return json.dumps(value, default=_json_default)


def cache_get(name):
    if not _enabled():
        return None
    try:
        raw = get_redis().get(_key(name))
    except Exception:  # noqa: BLE001 - fail open
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def cache_set(name, value, ttl=None):
    if not _enabled():
        return
    ttl = ttl if ttl is not None else getattr(settings, "CACHE_TTL_DEFAULT", 60)
    try:
        get_redis().set(_key(name), _dump(value), ex=ttl)
    except Exception:  # noqa: BLE001 - fail open
        pass


def cache_delete(name):
    if not _enabled():
        return
    try:
        get_redis().delete(_key(name))
    except Exception:  # noqa: BLE001
        pass


def cache_get_or_set(name, ttl, producer):
    """Return a cached value or compute + cache it, guarding the hot key.

    Under concurrency only one request runs ``producer``; the rest poll the
    short-lived lock and reuse its result (or fall back to computing).
    """
    value = cache_get(name)
    if value is not None:
        return value
    lock = _key(name) + ":lock"
    got_lock = False
    try:
        got_lock = bool(
            get_redis().set(lock, "1", nx=True, px=_LOCK_TTL_MS)
        )
    except Exception:  # noqa: BLE001 - fail open
        got_lock = False
    if got_lock:
        try:
            value = producer()
            cache_set(name, value, ttl)
            return value
        finally:
            try:
                get_redis().delete(lock)
            except Exception:  # noqa: BLE001
                pass
    deadline = time.monotonic() + (_POLL_MAX_MS / 1000)
    while time.monotonic() < deadline:
        value = cache_get(name)
        if value is not None:
            return value
        time.sleep(_POLL_INTERVAL_MS)
    return producer()


def enqueue_once(task, task_args=None, *, ttl=60):
    """Enqueue a Celery task but dedupe identical pending jobs in Redis.

    Returns True when the task was actually enqueued, False when a duplicate
    (same task + args) is already queued/in-flight. This collapses bursts of
    identical work (e.g. N students refreshing the same slot) into a single
    job. Falls back to always enqueueing when Redis is unavailable.
    """
    try:
        from celery import current_app

        dedupe_key = _key(
            "jobs:" + task + ":" + hashlib.sha1(json.dumps(task_args or [], default=_json_default).encode()).hexdigest()
        )
        got = bool(get_redis().set(dedupe_key, "1", nx=True, ex=ttl))
        if not got:
            return False
        try:
            current_app.send_task(task, args=task_args or [])
        except Exception:  # noqa: BLE001
            cache_delete(dedupe_key)
            raise
        return True
    except Exception:  # noqa: BLE001 - fail open: always enqueue
        from celery import current_app

        current_app.send_task(task, args=task_args or [])
        return True


def bump_slot_version():
    """Bump the slot-search generation so cached listings are recomputed."""
    if not _enabled():
        return
    try:
        get_redis().incr(_key(_SLOT_VERSION_KEY))
    except Exception:  # noqa: BLE001 - fail open
        pass


def get_slot_version():
    if not _enabled():
        return 0
    try:
        return int(get_redis().get(_key(_SLOT_VERSION_KEY)) or 0)
    except Exception:  # noqa: BLE001
        return 0


def slot_search_key(signature):
    return f"slot:search:v{get_slot_version()}:{signature}"


def incr_daily(prefix, when=None, ttl_days=45):
    """Increment a per-day Redis counter (rolls over automatically by date key).

    Used to track lightweight activity metrics (e.g. slot searches) without a
    DB table. Keys expire after ``ttl_days`` so the window stays bounded.
    """
    if not _enabled():
        return
    key = _key(f"stats:{prefix}:{(when or datetime.date.today()).isoformat()}")
    try:
        pipe = get_redis().pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl_days * 86400)
        pipe.execute()
    except Exception:  # noqa: BLE001 - fail open
        pass


def daily_series(prefix, days=30, when=None):
    """Return ``[{date, value}]`` for the last ``days`` days (zero-filled)."""
    if not _enabled():
        return []
    end = when or datetime.date.today()
    start = end - datetime.timedelta(days=days - 1)
    keys = [
        _key(f"stats:{prefix}:{(start + datetime.timedelta(days=i)).isoformat()}")
        for i in range(days)
    ]
    try:
        values = get_redis().mget(keys)
    except Exception:  # noqa: BLE001 - fail open
        values = [None] * days
    return [
        {
            "date": (start + datetime.timedelta(days=i)).isoformat(),
            "value": int(values[i] or 0),
        }
        for i in range(days)
    ]


def get_regions():
    return cache_get_or_set(
        "locations:regions",
        getattr(settings, "REGIONS_CACHE_TTL", 3600),
        producer=_load_regions,
    )


def get_districts(region_id=None):
    name = f"locations:districts:{region_id or 'all'}"
    return cache_get_or_set(
        name,
        getattr(settings, "REGIONS_CACHE_TTL", 3600),
        producer=lambda: _load_districts(region_id),
    )


def get_wards(district_id=None):
    name = f"locations:wards:{district_id or 'all'}"
    return cache_get_or_set(
        name,
        getattr(settings, "REGIONS_CACHE_TTL", 3600),
        producer=lambda: _load_wards(district_id),
    )


def get_verification_status(user_id, producer):
    """Cache a per-user validation result (e.g. profile verification status)."""
    return cache_get_or_set(
        f"validation:verification:user:{user_id}",
        getattr(settings, "VERIFICATION_CACHE_TTL", 300),
        producer=producer,
    )


def invalidate_user_validation(user_id):
    cache_delete(f"validation:verification:user:{user_id}")


def invalidate_locations():
    cache_delete("locations:regions")
    cache_delete("locations:districts:all")


def _load_regions():
    from apps.locations.models import Region

    return list(Region.objects.values("id", "name", "slug").order_by("name"))


def _load_districts(region_id):
    from apps.locations.models import District

    qs = District.objects.all()
    if region_id:
        qs = qs.filter(region_id=region_id)
    return list(qs.values("id", "name", "region_id").order_by("name"))


def _load_wards(district_id):
    from apps.locations.models import Ward

    qs = Ward.objects.all()
    if district_id:
        qs = qs.filter(district_id=district_id)
    return list(qs.values("id", "name", "district_id").order_by("name"))


def get_institutions():
    return cache_get_or_set(
        "institutions:list",
        getattr(settings, "REGIONS_CACHE_TTL", 3600),
        producer=lambda: _load_institutions(),
    )


def _load_institutions():
    import json

    from django.conf import settings

    with open(settings.INSTITUTIONS_DATA_FILE, encoding="utf-8") as fh:
        return json.load(fh)["institutions"]