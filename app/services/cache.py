"""Small in-process TTL cache.

Upstream event data changes on the order of hours, but a user refining filters
generates bursts of near-identical queries. Caching keeps us well inside trial
rate limits and makes the UI feel instant.

Deliberately in-process: no Redis dependency for a single-node app. The
interface is narrow enough to swap for a shared cache later.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any


class TTLCache:
    def __init__(self, ttl_seconds: int = 300, max_entries: int = 512):
        self._ttl = ttl_seconds
        self._max = max_entries
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() > expires_at:
            del self._data[key]
            return None
        self._data.move_to_end(key)  # LRU on read
        return value

    def set(self, key: str, value: Any) -> None:
        self._data[key] = (time.monotonic() + self._ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)  # evict least-recently-used

    def clear(self) -> None:
        self._data.clear()
