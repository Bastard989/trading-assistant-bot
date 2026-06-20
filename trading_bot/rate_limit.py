from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable


class SlidingWindowLimiter:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]:
        now = self.clock()
        cutoff = now - window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, int(events[0] + window_seconds - now) + 1)
                return False, retry_after
            events.append(now)
            return True, 0
