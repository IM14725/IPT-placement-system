import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.auth import validate_token
from app.core.redis import NOTIFY_CHANNEL, get_pub

router = APIRouter()

connections: dict[int, set[WebSocket]] = {}


async def notify_subscriber_task():
    pubsub = get_pub().pubsub()
    await pubsub.subscribe(NOTIFY_CHANNEL)
    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            try:
                payload = json.loads(message["data"])
            except json.JSONDecodeError:
                continue
            user_id = payload.get("user_id")
            for ws in list(connections.get(user_id, set())):
                try:
                    await ws.send_json(payload)
                except Exception:  # noqa: BLE001
                    connections.get(user_id, set()).discard(ws)
    finally:
        await pubsub.unsubscribe(NOTIFY_CHANNEL)


@router.websocket("/ws/notifications/{user_id}")
async def notifications(websocket: WebSocket, user_id: int):
    token = websocket.query_params.get("token")
    if not await validate_token(token, user_id):
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
    while True:
        try:
            await notify_subscriber_task()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            await asyncio.sleep(1)