"""
core/redis_client.py

Shared async Redis client. One pool, one client, reused across all
requests — Redis connections are expensive to open per-request.

REDIS_URL is required. The app refuses to start without a working
Redis (or Memurai on Windows) connection.

Used by:
  - Rate limiting middleware (app/middleware/subscription_guard.py)
  - permissions/guards.py: caching active subscription lookups to
    avoid a DB query on every gated request
  - tasks/celery_app.py: broker + result backend (configured directly
    via CELERY_BROKER_URL, not through this module — Celery manages
    its own connections)

Import pattern:
    from app.core.redis_client import get_redis

    @router.get("/search")
    async def search(redis = Depends(get_redis)):
        await redis.get("some_key")

Or outside a request (tasks, startup):
    from app.core.redis_client import redis_client
    await redis_client.ping()
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import redis.asyncio as aioredis
from redis.asyncio import Redis

from app.core.config import settings

# ── Client singleton ──────────────────────────────────────────────────────────


def _make_client() -> Redis:
    """
    Build the shared Redis client from config.

    REDIS_URL is required — the app refuses to start without it.

    decode_responses=True: all keys/values are Python str, not bytes.
    Without this, every get() returns b"value" and every comparison
    to a string literal fails silently (b"active" != "active").

    health_check_interval=30: pings the server every 30 seconds on
    idle connections — keeps them alive and detects network partitions
    before a request tries to use a dead socket.

    socket_connect_timeout / socket_timeout: bound how long a Redis
    operation blocks before raising. Without these, a Redis outage
    stalls the entire event loop indefinitely.
    """
    # ← CHANGE 1: Fail early with a clear message if REDIS_URL is missing
    if not settings.REDIS_URL:
        raise RuntimeError(
            "REDIS_URL is required. Set it in your .env file.\n"
            "Example: REDIS_URL=redis://localhost:6379/0\n"
            "Install Memurai on Windows or Redis on Linux/macOS."
        )
    return aioredis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        health_check_interval=30,
        socket_connect_timeout=5,
        socket_timeout=5,
        retry_on_timeout=True,
        max_connections=20,
    )


# Module-level singleton — imported directly for use outside requests.
redis_client: Redis = _make_client()


# ── FastAPI dependency ────────────────────────────────────────────────────────


async def get_redis() -> AsyncGenerator[Redis]:
    """
    Yields the shared Redis client as a FastAPI dependency.

    Does NOT open/close connections per-request — simply yields the
    module-level singleton so the connection pool is reused.

    Usage:
        from app.core.redis_client import get_redis
        from redis.asyncio import Redis

        @router.get("/me")
        async def me(redis: Redis = Depends(get_redis)):
            cached = await redis.get(f"user:{user.id}")
    """
    yield redis_client


# ── Startup / shutdown helpers ────────────────────────────────────────────────
# Called from app/main.py lifespan events.


async def connect_redis() -> None:
    """
    Validate the Redis connection at startup. Raises if the server is
    unreachable — surfaces misconfiguration immediately at startup
    rather than failing silently on the first request.

    app/main.py:
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            await connect_redis()
            yield
            await disconnect_redis()
    """
    # ← CHANGE 2: Catch all exceptions, not just RedisConnectionError.
    # A malformed URL can raise ConnectionRefusedError, OSError, etc.
    try:
        await redis_client.ping()
    except Exception as e:
        raise ConnectionError(
            f"Redis unreachable at {settings.REDIS_URL!r} — "
            "check REDIS_URL in .env and that Redis/Memurai is running.\n"
            f"Error: {type(e).__name__}: {e}"
        ) from e


async def disconnect_redis() -> None:
    """
    Close all pooled connections at shutdown. Without this, open sockets
    linger until the OS kills the process — fine in practice but noisy
    in tests and CI logs.
    """
    await redis_client.aclose()


# ── Key namespace helpers ─────────────────────────────────────────────────────
# Centralise key construction so a typo never causes a silent cache miss
# across two callers building the same key differently.


class RedisKeys:
    """
    Static factory methods for every Redis key pattern used in the app.

        key = RedisKeys.active_subscription(user_id)
        cached = await redis.get(key)

    TTL constants are collocated with the key definitions they govern.
    """

    # 5 min: short enough that Celery expiry updates are visible within
    # one TTL; long enough to eliminate per-request DB subscription queries.
    SUBSCRIPTION_TTL_SECONDS: int = 300

    # Rate-limit sliding windows (Standard 21 limits):
    #   login 5/min, register 5/min, search 60/min, default 100/min
    RATE_LIMIT_TTL_SECONDS: int = 60

    # KYC status rarely changes — cache for 10 minutes.
    KYC_STATUS_TTL_SECONDS: int = 600

    @staticmethod
    def active_subscription(user_id: str) -> str:
        """Does this user have an active subscription?"""
        return f"sub:active:{user_id}"

    @staticmethod
    def rate_limit(prefix: str, identifier: str) -> str:
        """Sliding window counter. prefix: 'login'|'register'|'search'|'api'"""
        return f"rl:{prefix}:{identifier}"

    @staticmethod
    def kyc_status(user_id: str) -> str:
        """What is this user's current kyc_status string?"""
        return f"kyc:status:{user_id}"

    @staticmethod
    def refresh_token_blacklist(jti: str) -> str:
        """
        Entry exists = this refresh token jti is revoked.
        For "log out everywhere" / single-device logout.
        TTL should match REFRESH_TOKEN_EXPIRE_DAYS in seconds.
        """
        return f"rt:blacklist:{jti}"
