import json

import redis.asyncio as aioredis

from app.core.config import settings

NOTIFY_CHANNEL = "ipt:notify"

_pub: aioredis.Redis | None = None


def get_pub() -> aioredis.Redis:
    global _pub
    if _pub is None:
        _pub = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _pub


async def publish_notification(payload: dict) -> None:
    await get_pub().publish(NOTIFY_CHANNEL, json.dumps(payload, default=str))


async def acquire_lock(name: str, ttl_ms: int = 30000) -> bool:
    """Atomically acquire a Redis mutex (SET NX PX)."""
    try:
        return bool(
            await get_pub().set(name, "1", nx=True, px=ttl_ms)
        )
    except Exception:  # noqa: BLE001
        return True


async def release_lock(name: str) -> None:
    try:
        await get_pub().delete(name)
    except Exception:  # noqa: BLE001
        pass