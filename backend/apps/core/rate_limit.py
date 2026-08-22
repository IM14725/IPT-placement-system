"""Redis-backed token bucket rate limiter with timer-based refill.

Each bucket stores ``tokens`` + ``last_refill``; on every request the tokens
are refilled by ``refill_rate * elapsed`` (capped at the bucket capacity) and
then decremented by the cost. Refill happens on access ("timer refill"), so a
quiet bucket simply grows back to full capacity over time. The check is atomic
(Lua) so concurrent requests cannot over-draw the bucket.
"""

import time
from functools import wraps

from django.conf import settings
from django.http import JsonResponse
from rest_framework.throttling import BaseThrottle

from apps.core.redis_client import get_redis

_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local refill = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local ttl_ms = tonumber(ARGV[5])

local tokens = redis.call('HGET', key, 'tokens')
local last = redis.call('HGET', key, 'last')
if not tokens then tokens = capacity else tokens = tonumber(tokens) end
if not last then last = now else last = tonumber(last) end

local elapsed_ms = math.max(0, now - last)
tokens = math.min(capacity, tokens + (elapsed_ms / 1000.0) * refill)
redis.call('HSET', key, 'tokens', tokens, 'last', now)
redis.call('PEXPIRE', key, ttl_ms)

if tokens >= cost then
  tokens = tokens - cost
  redis.call('HSET', key, 'tokens', tokens)
  return {1, tokens, 0}
else
  local retry_ms = math.ceil(((cost - tokens) / refill) * 1000)
  return {0, tokens, retry_ms}
end
"""


class RateLimitResult:
    __slots__ = ("allowed", "remaining", "retry_after")

    def __init__(self, allowed, remaining, retry_after):
        self.allowed = allowed
        self.remaining = remaining
        self.retry_after = retry_after


def consume(namespace, bucket, capacity, refill_per_second, cost=1.0, now=None):
    """Attempt to draw ``cost`` tokens from the bucket. Returns RateLimitResult.

    ``capacity``: max tokens the bucket holds. ``refill_per_second``: tokens
    added per second while the bucket is below capacity.
    """
    now = int(now * 1000) if now is not None else int(time.time() * 1000)
    key = f"{settings.CACHE_KEY_PREFIX}:rl:{namespace}:{bucket}"
    ttl_ms = int(capacity / max(refill_per_second, 1e-9) * 1000) + 5000
    try:
        allowed, remaining, retry_ms = get_redis().eval(
            _TOKEN_BUCKET_LUA,
            1,
            key,
            now,
            float(capacity),
            float(refill_per_second),
            float(cost),
            int(ttl_ms),
        )
        return RateLimitResult(bool(allowed), float(remaining), int(retry_ms) / 1000.0)
    except Exception:  # noqa: BLE001 - fail open when Redis is unavailable
        return RateLimitResult(True, capacity, 0)


def _default_bucket(request):
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return f"user:{user.pk}"
    return f"ip:{request.META.get('REMOTE_ADDR', 'unknown')}"


def token_bucket(
    capacity,
    refill_per_second,
    cost=1.0,
    scope="default",
    key_fn=None,
    deny_view=None,
    methods=None,
):
    """Decorator for function views. Denies with 429 (or ``deny_view``).

    ``methods`` limits enforcement to the given HTTP methods (e.g. ("POST",));
    other methods pass through untouched.
    """

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if methods is not None and request.method not in methods:
                return view(request, *args, **kwargs)
            if not getattr(settings, "RATE_LIMIT_ENABLED", True):
                return view(request, *args, **kwargs)
            bucket = key_fn(request) if key_fn else _default_bucket(request)
            result = consume(scope, bucket, capacity, refill_per_second, cost)
            request._rate_limit = result
            if not result.allowed:
                if deny_view is not None:
                    return deny_view(request, result)
                response = JsonResponse(
                    {"detail": "Rate limit exceeded. Please try again later."},
                    status=429,
                )
                response["Retry-After"] = str(int(result.retry_after))
                return response
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


class TokenBucketThrottle(BaseThrottle):
    """DRF throttle using the Redis token bucket. Override the class attrs.

    Example:
        class SlotSearchThrottle(TokenBucketThrottle):
            scope = "slot-search"
            capacity = 120
            refill_per_second = 20.0
    """

    scope = "default"
    capacity = 60
    refill_per_second = 1.0
    cost = 1.0

    def get_bucket(self, request, view):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            return f"user:{user.pk}"
        return f"ip:{request.META.get('REMOTE_ADDR', 'unknown')}"

    def allow_request(self, request, view):
        if not getattr(settings, "RATE_LIMIT_ENABLED", True):
            return True
        result = consume(
            self.scope,
            self.get_bucket(request, view),
            self.capacity,
            self.refill_per_second,
            self.cost,
        )
        self._result = result
        request._rate_limit = result
        return result.allowed

    def wait(self):
        result = getattr(self, "_result", None)
        return result.retry_after if result else None