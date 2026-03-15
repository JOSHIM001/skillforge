import json
from typing import Any

from redis.asyncio import Redis, from_url
from redis.asyncio.client import PubSub

from config import settings

# ── Singleton connection ──────────────────────────────────────────────────────
# redis.asyncio reuses a single connection pool across the application.
# Do NOT create a new Redis() per request — import this `redis` instance.
redis: Redis = from_url(
    settings.redis_url,
    encoding="utf-8",
    decode_responses=True,   # all get/set values are str, not bytes
    max_connections=20,
)


# ── Typed helpers ─────────────────────────────────────────────────────────────
async def cache_get(key: str) -> Any | None:
    """
    Get a JSON-decoded value from Redis.
    Returns None on cache miss or decode error.
    """
    raw = await redis.get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw  # return raw string if not JSON


async def cache_set(key: str, value: Any, ttl: int = 3600) -> None:
    """
    JSON-encode and store a value with a TTL (seconds).
    TTL=0 means no expiry.
    """
    serialized = json.dumps(value) if not isinstance(value, str) else value
    if ttl > 0:
        await redis.setex(key, ttl, serialized)
    else:
        await redis.set(key, serialized)


async def cache_delete(key: str) -> None:
    await redis.delete(key)


async def cache_delete_pattern(pattern: str) -> int:
    """Delete all keys matching a glob pattern. Returns count deleted."""
    keys = await redis.keys(pattern)
    if not keys:
        return 0
    return await redis.delete(*keys)


# ── Pub/Sub factory ───────────────────────────────────────────────────────────
def get_pubsub() -> PubSub:
    """
    Returns a new PubSub object bound to the shared Redis connection.
    Each WebSocket room subscription should get its own PubSub instance.
    """
    return redis.pubsub()


# ── Health check ──────────────────────────────────────────────────────────────
async def ping_redis() -> bool:
    """Returns True if Redis is reachable. Used in /health endpoint."""
    try:
        return await redis.ping()
    except Exception:
        return False


# ── Startup / shutdown ────────────────────────────────────────────────────────
async def close_redis() -> None:
    """Close the Redis connection pool. Called from main.py lifespan."""
    await redis.aclose()