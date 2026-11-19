import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.auth import validate_ticket
from app.core.redis import NOTIFY_CHANNEL, get_pub

logger = logging.getLogger(__name__)

router = APIRouter()

connections: dict[str, set[WebSocket]] = {}


async def notify_subscriber_task():
    pubsub = get_pub().pubsub()
    try:
        await pubsub.subscribe(NOTIFY_CHANNEL)
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            try:
                payload = json.loads(message["data"])
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            user_id = payload.get("user_id")
            revoked = payload.get("type") == "session_revoked"
            for ws in list(connections.get(user_id, set())):
                try:
                    await ws.send_json(payload)
                    if revoked:
                        # Django already destroyed the session; drop the socket too.
                        await ws.close(code=4401)
                        connections.get(user_id, set()).discard(ws)
                except Exception:  # noqa: BLE001
                    connections.get(user_id, set()).discard(ws)
    finally:
        # aclose() hands the connection back to the pool. Without it an attempt that
        # failed after getting its connection kept one of the pool's 100 for good;
        # once they were gone every Redis call in this process failed, so ticket
        # redemption returned 403 until the service was restarted.
        await pubsub.aclose()


@router.websocket("/ws/notifications/{user_id}")
async def notifications(websocket: WebSocket, user_id: str):
    ticket = websocket.query_params.get("ticket")
    if not await validate_ticket(ticket, user_id):
        await websocket.close(code=4401)
        return

    await websocket.accept()
    connections.setdefault(user_id, set()).add(websocket)
    try:
        await websocket.send_json({"type": "connected", "user_id": user_id})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        connections.get(user_id, set()).discard(websocket)


async def _subscriber_runner():
    failing = False
    while True:
        started = time.monotonic()
        try:
            await notify_subscriber_task()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            if time.monotonic() - started > 5:
                failing = False  # it had been subscribed, so this is a new failure
            if not failing:
                # Once per outage, not once per retry.
                logger.warning("Redis notification subscriber failed, retrying every second: %r", exc)
                failing = True
            await asyncio.sleep(1)