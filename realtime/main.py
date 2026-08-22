import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.webhooks import router as webhooks_router
from app.ws import router as ws_router
from app.ws.router import _subscriber_runner


@asynccontextmanager
async def lifespan(app: FastAPI):
    subscriber = asyncio.create_task(_subscriber_runner())
    yield
    subscriber.cancel()
    try:
        await subscriber
    except asyncio.CancelledError:
        pass


app = FastAPI(title="IPT Marketplace Realtime", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webhooks_router.router)
app.include_router(ws_router.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "ipt-realtime"}