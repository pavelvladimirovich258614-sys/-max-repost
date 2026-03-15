"""Simple in-memory rate limiter using asyncio (without Redis for first stage)."""

import asyncio
import time
from collections import deque

from loguru import logger

from config.settings import settings


class SimpleRateLimiter:
    """
    Simple in-memory rate limiter using token bucket algorithm.

    Allows up to max_rps requests per second with burst capability.
    Uses asyncio for synchronization - no Redis required.

    This is a simplified version for single-instance deployments.
    For distributed systems, migrate to TokenBucketRateLimiter with Redis.

    Attributes:
        max_rps: Maximum requests per second allowed
        burst: Maximum burst size (default: max_rps * 2)
    """

    def __init__(self, max_rps: int | None = None, burst: int | None = None) -> None:
        """
        Initialize rate limiter.

        Args:
            max_rps: Maximum requests per second (defaults to settings.max_rps)
            burst: Maximum burst size (defaults to max_rps * 2)
        """
        self.max_rps = max_rps or settings.max_rps
        self.burst = burst or (self.max_rps * 2)

        # Token bucket state
        self._tokens: float = float(self.burst)
        self._last_update: float = time.time()
        self._lock = asyncio.Lock()

        logger.debug(f"RateLimiter initialized: {self.max_rps} rps, burst={self.burst}")

    async def _refill_tokens(self) -> None:
        """
        Refill tokens based on elapsed time.

        Token bucket algorithm: tokens accumulate at max_rps rate
        up to the burst capacity.
        """
        now = time.time()
        elapsed = now - self._last_update

        # Calculate new tokens (rate * time)
        new_tokens = elapsed * self.max_rps

        # Add tokens, but don't exceed burst capacity
        self._tokens = min(self.burst, self._tokens + new_tokens)
        self._last_update = now

    async def acquire(self) -> bool:
        """
        Try to acquire a token.

        Returns:
            True if token acquired, False otherwise
        """
        async with self._lock:
            await self._refill_tokens()

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                logger.trace(f"Token acquired: {self._tokens:.1f} remaining")
                return True

            logger.trace(f"No tokens available: {self._tokens:.1f}")
            return False

    async def wait(self) -> None:
        """
        Wait until a token is available.

        Blocks the calling coroutine until rate limit allows a request.
        Implements simple spinning with small delays to avoid busy waiting.
        """
        max_wait = 5.0  # Maximum seconds to wait
        start_time = time.time()

        while True:
            if await self.acquire():
                return

            # Check timeout
            elapsed = time.time() - start_time
            if elapsed >= max_wait:
                logger.warning(f"Rate limiter timeout after {max_wait}s")
                raise TimeoutError(f"Rate limiter timeout: waited {max_wait}s without token")

            # Calculate time needed for one token
            # If we have partial tokens, wait less. If empty, wait for full token.
            async with self._lock:
                tokens_needed = 1.0 - self._tokens
                wait_time = tokens_needed / self.max_rps if tokens_needed > 0 else 0.01

            # Cap wait time to avoid long sleeps
            wait_time = min(max(wait_time, 0.01), 0.5)

            await asyncio.sleep(wait_time)

    async def close(self) -> None:
        """Cleanup (no-op for in-memory limiter)."""
        pass

    async def __aenter__(self) -> "SimpleRateLimiter":
        """Context manager entry."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Context manager exit."""
        await self.close()


# Backward compatibility aliases
TokenBucketRateLimiter = SimpleRateLimiter
RateLimiter = SimpleRateLimiter
