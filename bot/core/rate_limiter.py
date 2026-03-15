"""Rate limiter for API requests using token bucket algorithm."""

import asyncio
import time
from dataclasses import dataclass

import redis.asyncio as aioredis
from loguru import logger

from config.settings import settings


@dataclass
class TokenBucketRateLimiter:
    """
    Token bucket rate limiter using Redis.

    Allows up to max_rps requests per second with burst capability.
    Uses Redis for distributed rate limiting across multiple instances.

    Attributes:
        redis_url: Redis connection URL
        max_rps: Maximum requests per second allowed
        key: Redis key for storing bucket state
    """

    redis_url: str
    max_rps: int = 25
    key: str = "rate_limit:max_api"

    def __init__(self, redis_url: str | None = None, max_rps: int | None = None) -> None:
        """
        Initialize rate limiter.

        Args:
            redis_url: Redis connection URL (defaults to settings.redis_url)
            max_rps: Maximum requests per second (defaults to settings.max_rps)
        """
        self.redis_url = redis_url or settings.redis_url
        self.max_rps = max_rps or settings.max_rps
        self.key = f"rate_limit:max_api:{self.max_rps}"
        self._redis: aioredis.Redis | None = None
        self._lock = asyncio.Lock()

    async def _get_redis(self) -> aioredis.Redis:
        """
        Get or create Redis connection.

        Returns:
            Redis connection
        """
        if self._redis is None:
            self._redis = await aioredis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        return self._redis

    async def _get_token(self) -> bool:
        """
        Try to acquire a token from the bucket.

        Uses Redis INCR with expiration to implement token bucket.
        Each token represents one request permit.

        Returns:
            True if token acquired, False otherwise
        """
        redis = await self._get_redis()

        # Current timestamp in milliseconds
        now = int(time.time() * 1000)

        # Redis pipeline for atomic operations
        pipe = redis.pipeline()

        # Get current count
        pipe.get(f"{self.key}:count")
        # Get last reset time
        pipe.get(f"{self.key}:reset")

        results = await pipe.execute()

        count = int(results[0] or 0)
        last_reset = int(results[1] or 0)

        # Check if we need to reset (new second window)
        window_start = now - (now % 1000)
        if last_reset < window_start:
            # New window - reset counter
            async with self._lock:
                # Double-check after acquiring lock
                current_reset = await redis.get(f"{self.key}:reset")
                if current_reset is None or int(current_reset) < window_start:
                    await redis.set(f"{self.key}:count", "0", px=2000)
                    await redis.set(f"{self.key}:reset", str(window_start), px=2000)
                    count = 0

        # Try to increment
        new_count = await redis.incr(f"{self.key}:count")

        if new_count > self.max_rps:
            # Too many requests - decrement back
            await redis.decr(f"{self.key}:count")
            logger.debug(f"Rate limit exceeded: {new_count}/{self.max_rps}")
            return False

        logger.trace(f"Token acquired: {new_count}/{self.max_rps}")
        return True

    async def wait(self) -> None:
        """
        Wait until a token is available.

        Blocks the calling coroutine until rate limit allows a request.
        Implements simple spinning with small delays to avoid busy waiting.
        """
        max_wait = 5.0  # Maximum seconds to wait
        start_time = time.time()

        while True:
            if await self._get_token():
                return

            # Check timeout
            elapsed = time.time() - start_time
            if elapsed >= max_wait:
                logger.warning(f"Rate limiter timeout after {max_wait}s")
                raise TimeoutError(f"Rate limiter timeout: waited {max_wait}s without token")

            # Small delay before retry (exponential backoff within window)
            delay = min(0.1, elapsed * 0.1)
            await asyncio.sleep(delay)

    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis is not None:
            await self._redis.close()
            self._redis = None

    async def __aenter__(self) -> "TokenBucketRateLimiter":
        """Context manager entry."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Context manager exit."""
        await self.close()


# Backward compatibility alias
RateLimiter = TokenBucketRateLimiter
