from sqlalchemy import text

from app.core.db import SessionLocal


async def validate_token(token: str | None, user_id: int) -> bool:
    """Validate a DRF Token (authtoken_token table) against the shared DB."""
    if not token:
        return False
    async with SessionLocal() as session:
        result = await session.execute(
            text(
                "SELECT t.user_id, u.is_active FROM authtoken_token t "
                "JOIN accounts_user u ON u.id = t.user_id WHERE t.key = :key"
            ),
            {"key": token},
        )
        row = result.first()
    return bool(row and row.user_id == user_id and row.is_active)