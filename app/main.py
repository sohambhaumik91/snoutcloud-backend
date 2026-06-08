from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import (
    registration,
    rescan,
    inference,
    dogs,
)
from app.services.redis_client import get_redis
from app.services.embedding import get_model
from app.services.queue import get_arq_pool, close_arq_pool
import asyncio


@asynccontextmanager
async def lifespan(app: FastAPI):
    # warm up text embedding model (loads weights into memory once)
    await asyncio.to_thread(get_model)
    # NOTE: the nose biometric encoder is NOT warmed here — inference runs in
    # the arq worker process (app/worker.py), so only the worker loads the
    # 380MB checkpoint. The web process just enqueues jobs.
    # verify Redis is reachable
    r = get_redis()
    await r.ping()
    # create the arq enqueue pool up front so /inference/start fails fast if
    # Redis is unreachable rather than on first enqueue.
    await get_arq_pool()
    yield
    # shutdown: close the arq pool and Redis connection pool
    await close_arq_pool()
    await r.aclose()


app = FastAPI(
    title="PawLog API",
    description="Dog health memory — GraphRAG backend",
    version="0.1.0",
    lifespan=lifespan
)

# Permissive CORS for local dev: any localhost/127.0.0.1 port, any ngrok URL,
# any Expo/RN dev origin. allow_credentials=True requires an explicit origin
# match (wildcard "*" is rejected by browsers when credentials are on), so we
# use a regex instead of allow_origins=["*"].
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?|https?://.*\.ngrok(-free)?\.(app|io)|exp://.*)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=600,
)

app.include_router(registration.router)
app.include_router(rescan.router)
app.include_router(inference.router)
app.include_router(dogs.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
