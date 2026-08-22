"""Hot-key cache coordination for the FastAPI payment webhook.

Django reads the slot-search generation from Redis (``ipt:slot:cache:version``)
and keys its cached listings by it, so a single INCR here invalidates every
cached slot listing immediately. The webhook writes slots/applications via raw
SQL (bypassing Django signals), so it must bump this version itself.
"""

from app.core.config import settings
from app.core.redis import get_pub

SLOT_VERSION_KEY = "slot:cache:version"


async def bump_slot_version() -> None:
    client = get_pub()
    await client.incr(f"{settings.cache_key_prefix}:{SLOT_VERSION_KEY}")