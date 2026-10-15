"""In-memory rate limiting for expensive analytics query endpoints.

NOTE: This rate limiter uses in-memory sliding window counters per client IP.
It is designed for single-instance deployments and is NOT synchronized across
multiple independent application worker processes or multi-container replicas.
"""
import logging
import math
import threading
import time
from typing import Dict, List, Optional, Tuple
from fastapi import HTTPException, Request, status

from src.core.config import get_settings

logger = logging.getLogger(__name__)


class InMemoryRateLimiter:
    """Thread-safe in-memory sliding-window rate limiter with automatic expired entry pruning."""

    def __init__(self, window_seconds: float = 60.0):
        self.window_seconds = window_seconds
        self._requests: Dict[str, List[float]] = {}
        self._lock = threading.Lock()
        self._last_cleanup = 0.0

    def _prune_expired_locked(self, cutoff: float) -> int:
        """Prune client entries whose timestamps have all expired outside cutoff. Must be called with _lock."""
        pruned_count = 0
        keys_to_remove = []
        for key, timestamps in self._requests.items():
            valid = [t for t in timestamps if t > cutoff]
            if not valid:
                keys_to_remove.append(key)
            else:
                self._requests[key] = valid

        for key in keys_to_remove:
            del self._requests[key]
            pruned_count += 1
        return pruned_count

    def cleanup(self) -> int:
        """Explicitly purge all expired client records from memory. Returns count of purged client keys."""
        now = time.time()
        cutoff = now - self.window_seconds
        with self._lock:
            self._last_cleanup = now
            return self._prune_expired_locked(cutoff)

    def is_allowed(self, client_key: str, max_requests: int) -> Tuple[bool, int]:
        """
        Check if request is allowed for client_key within sliding window.
        
        Returns:
            (is_allowed: bool, retry_after_seconds: int)
        """
        now = time.time()
        window_start = now - self.window_seconds

        with self._lock:
            # Periodic cleanup of expired clients to prevent unbounded memory growth
            if now - self._last_cleanup > self.window_seconds:
                self._prune_expired_locked(window_start)
                self._last_cleanup = now

            timestamps = self._requests.get(client_key, [])
            # Prune timestamps outside current window
            valid_timestamps = [t for t in timestamps if t > window_start]

            if len(valid_timestamps) >= max_requests:
                oldest_in_window = valid_timestamps[0]
                retry_after = max(1, int(math.ceil(oldest_in_window + self.window_seconds - now)))
                self._requests[client_key] = valid_timestamps
                return False, retry_after

            valid_timestamps.append(now)
            self._requests[client_key] = valid_timestamps
            return True, 0

    def reset(self) -> None:
        """Clear all rate limiting state (useful for tests)."""
        with self._lock:
            self._requests.clear()
            self._last_cleanup = 0.0


# Global rate limiter instance
limiter = InMemoryRateLimiter(window_seconds=60.0)


def get_client_ip(request: Request, trust_proxy_headers: bool = False) -> str:
    """
    Extract client IP address safely.
    
    Only inspects X-Forwarded-For if trust_proxy_headers is explicitly True.
    Otherwise uses the direct socket connection host to prevent IP spoofing.
    """
    if trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # First IP in comma-separated list is the original client
            return forwarded.split(",")[0].strip()

    if request.client and request.client.host:
        return request.client.host

    return "127.0.0.1"


def check_rate_limit(request: Request) -> None:
    """
    FastAPI dependency to enforce rate limits on incoming analytics requests.
    Raises HTTP 429 Too Many Requests if the limit is exceeded.
    """
    settings = get_settings()
    if not settings.RATE_LIMIT_ENABLED:
        return

    client_ip = get_client_ip(request, trust_proxy_headers=settings.TRUST_PROXY_HEADERS)
    allowed, retry_after = limiter.is_allowed(
        client_key=client_ip,
        max_requests=settings.RATE_LIMIT_REQUESTS_PER_MINUTE,
    )

    if not allowed:
        logger.warning(
            "Rate limit exceeded for client IP %s (limit: %s/min). Retry after %s seconds.",
            client_ip,
            settings.RATE_LIMIT_REQUESTS_PER_MINUTE,
            retry_after,
            extra={
                "client_ip": client_ip,
                "retry_after": retry_after,
                "error_type": "RateLimitExceeded",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please wait before submitting more queries.",
            headers={"Retry-After": str(retry_after)},
        )
