"""In-process sliding-window rate limiter.

This is a backstop, not the first line of defence: the counters live in this process, so they
reset on restart and are per replica. The primary limit belongs to the ingress / Cloudflare.

Memory is bounded. When there are too many keys, expired ones are dropped first and then the
least recently used. It never wipes everything, so flooding it with junk keys cannot be used to
reset someone's counter wholesale.
"""

import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field

MAX_KEYS = 10_000
_SWEEP_EVERY = 1.0  # seconds; keeps a full table from being scanned on every call


@dataclass
class _Entry:
    window: float
    hits: deque[float] = field(default_factory=deque)

    def prune(self, now: float) -> None:
        cutoff = now - self.window
        while self.hits and self.hits[0] <= cutoff:
            self.hits.popleft()


class RateLimiter:
    def __init__(
        self, *, max_keys: int = MAX_KEYS, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._max_keys = max_keys
        self._clock = clock
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()
        self._last_sweep = float("-inf")

    def __len__(self) -> int:
        return len(self._entries)

    def retry_after(self, key: str, limit: int) -> int | None:
        """Seconds until `key` is allowed again, or None if it is under `limit` right now.
        Records nothing."""
        with self._lock:
            now = self._clock()
            entry = self._entries.get(key)
            if entry is None:
                return None
            entry.prune(now)
            return self._wait(entry, limit, now)

    def record(self, key: str, window: float) -> None:
        """Count one event for `key`."""
        with self._lock:
            now = self._clock()
            entry = self._entries.get(key)
            if entry is None:
                entry = self._entries[key] = _Entry(window)
                self._make_room(now, keep=key)
            entry.window = max(entry.window, window)
            entry.prune(now)
            entry.hits.append(now)
            self._entries.move_to_end(key)

    def hit(self, key: str, limit: int, window: float) -> int | None:
        """Count one event unless `key` is already at `limit`.

        Returns None if allowed, else the seconds to wait. Blocked attempts are not counted,
        so retrying does not extend the wait.
        """
        with self._lock:
            now = self._clock()
            entry = self._entries.get(key)
            if entry is not None:
                entry.prune(now)
                wait = self._wait(entry, limit, now)
                if wait is not None:
                    return wait
            else:
                entry = self._entries[key] = _Entry(window)
                self._make_room(now, keep=key)
            entry.window = max(entry.window, window)
            entry.hits.append(now)
            self._entries.move_to_end(key)
            return None

    def reset(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    @staticmethod
    def _wait(entry: _Entry, limit: int, now: float) -> int | None:
        if len(entry.hits) < limit:
            return None
        # Allowed again once the hit that makes the count reach `limit` ages out.
        oldest_relevant = entry.hits[len(entry.hits) - limit]
        return max(1, int(oldest_relevant + entry.window - now) + 1)

    def _make_room(self, now: float, keep: str) -> None:
        """Called with the lock held, right after `keep` was added (it has no hits yet, so it
        must not be mistaken for an expired entry)."""
        if len(self._entries) <= self._max_keys:
            return
        if now - self._last_sweep >= _SWEEP_EVERY:
            self._last_sweep = now
            for k in [k for k, e in self._entries.items() if k != keep and self._expired(e, now)]:
                del self._entries[k]
        while len(self._entries) > self._max_keys:
            self._entries.popitem(last=False)  # least recently used

    @staticmethod
    def _expired(entry: _Entry, now: float) -> bool:
        entry.prune(now)
        return not entry.hits
