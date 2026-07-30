"""Lightweight caching service.

A simple in-process TTL cache — no Redis or external cache dependency,
which would be overkill for a single-instance deployment serving 300-500
documents. Caches recent search results and frequently read metadata
(subject/module lists, repo sync status) and is invalidated automatically
whenever a GitHub sync updates the index.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable


class TTLCache:
    """Thread-safe in-memory cache with per-key time-to-live."""

    def __init__(self, default_ttl_seconds: float = 300.0, max_entries: int = 500) -> None:
        self.default_ttl_seconds = default_ttl_seconds
        self.max_entries = max_entries
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl_seconds: float | None = None) -> None:
        with self._lock:
            if len(self._store) >= self.max_entries and key not in self._store:
                self._evict_oldest()
            expires_at = time.monotonic() + (ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds)
            self._store[key] = (expires_at, value)

    def get_or_set(self, key: str, factory: Callable[[], Any], ttl_seconds: float | None = None) -> Any:
        cached = self.get(key)
        if cached is not None:
            return cached
        value = factory()
        self.set(key, value, ttl_seconds)
        return value

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def invalidate_prefix(self, prefix: str) -> None:
        with self._lock:
            for key in [k for k in self._store if k.startswith(prefix)]:
                del self._store[key]

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def _evict_oldest(self) -> None:
        if not self._store:
            return
        oldest_key = min(self._store, key=lambda k: self._store[k][0])
        del self._store[oldest_key]


# Process-wide cache instance shared across services.
cache = TTLCache(default_ttl_seconds=300.0, max_entries=1000)
