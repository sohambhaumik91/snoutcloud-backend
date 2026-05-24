from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import (
    documents,
    auth,
    dogs,
    episodes,
    conversations,
    registration,
    rescan,
    inference,
)
from app.services.redis_client import get_redis
from app.services.embedding import get_model
import asyncio


@asynccontextmanager
async def lifespan(app: FastAPI):
    # warm up embedding model (loads weights into memory once)
    await asyncio.to_thread(get_model)
    # verify Redis is reachable
    r = get_redis()
    await r.ping()
    yield
    # shutdown: close Redis connection pool
    await r.aclose()


app = FastAPI(
    title="PawLog API",
    description="Dog health memory — GraphRAG backend",
    version="0.1.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],    # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(dogs.router)
app.include_router(episodes.router)
app.include_router(conversations.router)
app.include_router(registration.router)
app.include_router(rescan.router)
app.include_router(inference.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
