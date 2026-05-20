"""Basic intrusion detection for API abuse."""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Dict

logger = logging.getLogger("openclaw.security.ids")


class RateLimiter:
    """Token bucket rate limiter per caller ID."""

    def __init__(self, max_per_minute: int = 30) -> None:
        self._max = max_per_minute
        self._buckets: Dict[str, deque] = defaultdict(deque)
        self._lock = Lock()

    def is_allowed(self, caller_id: str) -> bool:
        now = time.time()
        cutoff = now - 60.0
        with self._lock:
            bucket = self._buckets[caller_id]
            # Remove old entries
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self._max:
                logger.warning("Rate limit exceeded for caller=%s", caller_id)
                return False
            bucket.append(now)
            return True


class AnomalyDetector:
    """Detects unusual API usage patterns."""

    def __init__(self) -> None:
        self._failed_auth: Dict[str, int] = defaultdict(int)
        self._lock = Lock()

    def record_failed_auth(self, ip: str) -> int:
        with self._lock:
            self._failed_auth[ip] += 1
            count = self._failed_auth[ip]
            if count >= 5:
                logger.critical(
                    "SECURITY: %d failed auth attempts from %s", count, ip
                )
            return count

    def is_blocked(self, ip: str) -> bool:
        with self._lock:
            return self._failed_auth[ip] >= 10

    def reset(self, ip: str) -> None:
        with self._lock:
            self._failed_auth.pop(ip, None)
