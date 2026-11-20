"""In-process sliding-window rate limiter.

Good for a single-instance demo. For several replicas, replace the storage with Redis
(same interface: ``hit(key) -> (allowed, retry_after)``).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            bucket = self._hits[key]
            while bucket and now - bucket[0] >= self.window:
                bucket.popleft()
            if len(bucket) >= self.limit:
                retry_after = int(self.window - (now - bucket[0])) + 1
                return False, retry_after
            bucket.append(now)
            if len(self._hits) > 50_000:  # bound memory under key churn
                for k in [k for k, v in self._hits.items() if not v]:
                    del self._hits[k]
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
