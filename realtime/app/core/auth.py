import logging

from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import text

from app.core.db import SessionLocal
from app.core.redis import get_pub

logger = logging.getLogger(__name__)


async def validate_ticket(ticket: str | None, user_id: str) -> bool:
    """Redeem a single-use WebSocket ticket issued by Django (``apps/core/ws_tickets.py``).

    GETDEL makes redemption atomic, so a ticket can never be replayed.
    """
    if not ticket:
        return False
    key = f"ipt:wsticket:{ticket}"
    try:
        try:
            owner = await get_pub().getdel(key)
        except RedisConnectionError:
            # After a Redis restart an idle pooled connection is dead and the first
            # command on it fails; that failure drops it, so one more try reconnects.
            owner = await get_pub().getdel(key)
    except Exception:  # noqa: BLE001 - no Redis, no realtime auth
        logger.exception("WebSocket ticket could not be redeemed: Redis call failed")
        return False
    if not owner or owner != user_id:
        return False
    async with SessionLocal() as session:
        result = await session.execute(
            text("SELECT is_active FROM accounts_user WHERE id = CAST(:uid AS uuid)"),
            {"uid": owner},
        )
        row = result.first()
    return bool(row and row.is_active)
