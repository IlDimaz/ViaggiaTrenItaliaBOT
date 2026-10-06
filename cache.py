"""Tiny async TTL cache used to stay polite with the upstream (free) APIs."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, Tuple

_CacheEntry = Tuple[float, Any]


class TTLCache:
    """A minimal in-memory async-safe cache with per-key TTLs."""

    def __init__(self) -> None:
        self._store: Dict[str, _CacheEntry] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    async def _lock_for(self, key: str) -> asyncio.Lock:
        async with self._global_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at < time.monotonic():
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        self._store[key] = (time.monotonic() + ttl, value)

    async def get_or_set(
        self, key: str, ttl: float, factory: Callable[[], Awaitable[Any]]
    ) -> Any:
        """Return cached value or compute it once (single-flight per key)."""
        cached = self.get(key)
        if cached is not None:
            return cached
        lock = await self._lock_for(key)
        async with lock:
            cached = self.get(key)
            if cached is not None:
                return cached
            value = await factory()
            self.set(key, value, ttl)
            return value


# Shared cache instance.
cache = TTLCache()

# TTL constants (seconds).
TTL_STATIONS = 24 * 60 * 60     # station autocomplete / ids barely change
TTL_DEPARTURES = 60             # departures board
TTL_STATUS = 30                 # real-time train status
TTL_REGION = 24 * 60 * 60       # station -> region code
TTL_ALERTS = 300                # infomobilita news / ticker
TTL_SOLUTIONS = 120            # priced solutions
