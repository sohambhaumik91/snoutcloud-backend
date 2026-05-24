import json
import redis.asyncio as aioredis
from app.core.config import settings

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def publish(channel: str, data: dict) -> None:
    r = get_redis()
    await r.publish(channel, json.dumps(data))


async def subscribe(channel: str) -> aioredis.client.PubSub:
    r = get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(channel)
    return pubsub
