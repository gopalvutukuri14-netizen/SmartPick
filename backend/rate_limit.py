"""
Lightweight, thread-safe, bounded-memory in-process sliding-window rate limiter.

Designed for single-instance academic/MVP deployments without requiring Redis
or external infrastructure. Not suitable for multi-instance distributed rate
limiting across multiple worker processes.
"""

from __future__ import annotations

import os
import time
import threading
from collections import OrderedDict, deque
from typing import Optional

from fastapi import HTTPException, Request, status


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def is_rate_limiting_enabled() -> bool:
    return os.getenv("RATE_LIMIT_ENABLED", "true").strip().lower() not in ("false", "0", "no")


class InMemoryRateLimiter:
    """Thread-safe sliding-window rate limiter with bounded key storage."""

    def __init__(self, max_keys: int = 10_000) -> None:
        self._max_keys = max(100, max_keys)
        self._lock = threading.Lock()
        self._buckets: OrderedDict[str, deque[float]] = OrderedDict()

    def check_and_record(
        self,
        key: str,
        limit: int,
        window_seconds: int = 60,
        now: Optional[float] = None,
    ) -> tuple[bool, int]:
        """
        Check whether ``key`` is within ``limit`` requests in ``window_seconds``.

        Returns:
            (allowed, retry_after_seconds)
        """
        current = time.monotonic() if now is None else now
        cutoff = current - window_seconds

        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                if len(self._buckets) >= self._max_keys:
                    self._buckets.popitem(last=False)
                bucket = deque()
                self._buckets[key] = bucket
            else:
                self._buckets.move_to_end(key)

            while bucket and bucket[0] <= cutoff:
                bucket.popleft()

            if len(bucket) >= limit:
                oldest = bucket[0]
                retry_after = max(1, int((oldest + window_seconds) - current) + 1)
                return False, retry_after

            bucket.append(current)
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


rate_limiter = InMemoryRateLimiter()


def get_client_ip(request: Optional[Request]) -> str:
    """
    Extract client IP from ASGI connection metadata.
    Does not trust arbitrary client-supplied proxy headers (e.g. X-Forwarded-For).
    """
    if request is None or request.client is None or not request.client.host:
        return "unknown"
    return request.client.host


def enforce_rate_limit(
    *,
    scope: str,
    identifier: str,
    limit: int,
    window_seconds: int = 60,
) -> None:
    """Enforce a rate limit bucket and raise HTTP 429 with Retry-After on breach."""
    if not is_rate_limiting_enabled():
        return

    allowed, retry_after = rate_limiter.check_and_record(
        key=f"{scope}:{identifier}",
        limit=limit,
        window_seconds=window_seconds,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many requests. Please wait {retry_after} second(s) and try again.",
            headers={"Retry-After": str(retry_after)},
        )


def recommend_limit_per_min() -> int:
    return _env_int("RATE_LIMIT_RECOMMEND_PER_MIN", 20)


def auth_ip_limit_per_min() -> int:
    return _env_int("RATE_LIMIT_AUTH_IP_PER_MIN", 60)


def auth_account_limit_per_min() -> int:
    return _env_int("RATE_LIMIT_AUTH_ACCOUNT_PER_MIN", 10)
