"""Redis token-bucket rate limiter for the FastAPI layer.

Same algorithm as the Django side (``apps/core/rate_limit.py``): a bucket
refills by ``refill_per_second * elapsed`` up to its capacity, drawn atomically
in Lua. Used to protect the payment webhook against abuse/replay bursts.
"""

import time

from fastapi import HTTPException, Request

from app.core.config import settings
from app.core.redis import get_pub

TOKEN_BUCKET_LUA = """
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


async def consume(namespace, bucket, capacity, refill_per_second, cost=1.0, now=None):
    now = int(now * 1000) if now is not None else int(time.time() * 1000)
    key = f"{settings.cache_key_prefix}:rl:{namespace}:{bucket}"
    ttl_ms = int(capacity / max(refill_per_second, 1e-9) * 1000) + 5000
    try:
        allowed, remaining, retry_ms = await get_pub().eval(
            TOKEN_BUCKET_LUA,
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


def client_bucket(request: Request) -> str:
    """Bucket key from the caller: realtime callbacks carry the caller's IP."""
    forwarded = request.headers.get("x-forwarded-for")
    ip = (forwarded.split(",")[0] if forwarded else request.client.host if request.client else "unknown").strip()
    return f"ip:{ip}"


async def enforce(
    request: Request,
    scope: str,
    capacity: int,
    refill_per_second: float,
    cost: float = 1.0,
) -> None:
    result = await consume(scope, client_bucket(request), capacity, refill_per_second, cost)
    if not result.allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Try again in {max(1, int(result.retry_after))} seconds.",
            headers={"Retry-After": str(max(1, int(result.retry_after)))},
        )