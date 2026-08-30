"""tam.Cache -- an optional, pluggable cache for tam.Symbol/tam.query()
results. Caching is opt-in everywhere it's accepted (the default is
`cache=None`, meaning "always hit the connection, exactly like before this
existed") -- pass one of the implementations below, or write your own
(just implement get/set/clear), to skip re-running an identical query.

The motivating case: Colab. Re-running a cell re-runs the whole query
again by default, even if nothing about the underlying data changed --
mildly wasteful locally, actually slow against a real R2-backed
connection. Construct ONE cache object at the top of a notebook and pass
it to every `Symbol(...)`/`tam.query(...)` call for the rest of the
session:

    from tam import Symbol, ManualCache

    cache = ManualCache()
    Symbol("AAPL", cache=cache).minute_bars()   # hits the connection
    Symbol("AAPL", cache=cache).minute_bars()   # same call, same cache key -> cached, no re-fetch
    cache.clear()                                # explicit -- ManualCache never evicts on its own

Every implementation here keys on the exact (sql, params, engine) tuple
that would otherwise be sent to the connection -- not on anything
dataset-specific -- so correctness is automatic: two calls only ever
share a cache entry if they'd have produced byte-for-byte the same query.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from typing import Any, Hashable, Optional


class Cache(ABC):
    """Minimal cache contract every implementation below satisfies -- three
    methods, no assumptions about eviction policy. Implement this yourself
    for anything not covered here (e.g. a cache shared across processes)."""

    @abstractmethod
    def get(self, key: Hashable) -> Optional[Any]:
        """The cached value for `key`, or None if it's not present (a
        cache miss and "cached None" are indistinguishable -- fine here,
        since no query result this cache stores is ever meaningfully
        None itself)."""
        ...

    @abstractmethod
    def set(self, key: Hashable, value: Any) -> None: ...

    @abstractmethod
    def clear(self) -> None: ...


class ManualCache(Cache):
    """Never evicts anything on its own -- entries live until `clear()` is
    called explicitly, or the process ends. This is the one to reach for
    in a notebook: re-running a cell (or the whole notebook) within the
    same kernel session just reuses whatever's already cached, with no
    TTL/size logic to reason about or tune."""

    def __init__(self):
        self._store: dict[Hashable, Any] = {}

    def get(self, key: Hashable) -> Optional[Any]:
        return self._store.get(key)

    def set(self, key: Hashable, value: Any) -> None:
        self._store[key] = value

    def clear(self) -> None:
        self._store.clear()


class TTLCache(Cache):
    """Evicts a entry the first time it's read after `ttl_seconds` has
    elapsed since it was set (checked on read, not via a background
    sweep -- same "lazy expiry" shape as tam-data-explorer's own
    bucketStats()/folderItemCount() caches, just in Python here). Right
    for a long-running process (a server, a long notebook session) where
    the underlying data genuinely changes over time and a permanently
    stale ManualCache entry would be wrong, not just wasteful."""

    def __init__(self, ttl_seconds: float):
        self._ttl_seconds = ttl_seconds
        self._store: dict[Hashable, tuple[Any, float]] = {}

    def get(self, key: Hashable) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() >= expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: Hashable, value: Any) -> None:
        self._store[key] = (value, time.monotonic() + self._ttl_seconds)

    def clear(self) -> None:
        self._store.clear()


class LRUCache(Cache):
    """Bounded to `max_entries` -- evicts whichever entry was least
    recently GET (not set) once a new entry would exceed the cap. Right
    for "cache aggressively, but don't let a long loop over hundreds of
    tickers hold every single result in memory forever" -- unlike
    ManualCache/TTLCache, size is bounded regardless of how long the
    process runs or how many distinct queries it makes."""

    def __init__(self, max_entries: int):
        self._max_entries = max_entries
        self._store: "OrderedDict[Hashable, Any]" = OrderedDict()

    def get(self, key: Hashable) -> Optional[Any]:
        if key not in self._store:
            return None
        self._store.move_to_end(key)
        return self._store[key]

    def set(self, key: Hashable, value: Any) -> None:
        self._store[key] = value
        self._store.move_to_end(key)
        if len(self._store) > self._max_entries:
            self._store.popitem(last=False)

    def clear(self) -> None:
        self._store.clear()
