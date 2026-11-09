from sqlalchemy import text

from app.core.db import SessionLocal
from app.core.redis import get_pub


async def validate_ticket(ticket: str | None, user_id: str) -> bool:
    """Redeem a single-use WebSocket ticket issued by Django (``apps/core/ws_tickets.py``).

    GETDEL makes redemption atomic, so a ticket can never be replayed.
    """
    if not ticket:
        return False
    try:
        owner = await get_pub().getdel(f"ipt:wsticket:{ticket}")
    except Exception:  # noqa: BLE001 - no Redis, no realtime auth
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
