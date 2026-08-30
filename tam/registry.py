"""Generic (base_type, name) -> implementation registry.

New DataProvider/DataStore implementations self-register with a decorator instead of
being wired into a hardcoded lookup table (Open/Closed: adding one never requires
editing existing code).

Usage:
    @Registry.register(DataProvider, "fmp")
    class FMPProvider(DataProvider): ...

    provider = Registry.get(DataProvider, "fmp")               # cached singleton, no-arg construct
    provider = Registry[DataProvider, "fmp"]                   # sugar for Registry.get(...)
    store = Registry.create(DataStore, "parquet", "data/eod")  # fresh instance, args passed through
"""

from __future__ import annotations

from typing import TypeVar

T = TypeVar("T")
_Key = tuple[type, str]


class Registry:
    _classes: dict[_Key, type] = {}
    _singletons: dict[_Key, object] = {}

    @classmethod
    def register(cls, base_type: type, name: str):
        def decorator(impl: type[T]) -> type[T]:
            key = (base_type, name)
            if key in cls._classes:
                raise ValueError(f"{base_type.__name__} {name!r} is already registered")
            cls._classes[key] = impl
            return impl

        return decorator

    @classmethod
    def _lookup(cls, base_type: type, name: str) -> type:
        try:
            return cls._classes[(base_type, name)]
        except KeyError as exc:
            raise KeyError(
                f"no {base_type.__name__} registered as {name!r}; available: {cls.names(base_type)}"
            ) from exc

    @classmethod
    def create(cls, base_type: type, name: str, *args, **kwargs):
        """Construct a fresh instance on every call; use when construction needs per-call args."""
        return cls._lookup(base_type, name)(*args, **kwargs)

    @classmethod
    def get(cls, base_type: type, name: str):
        """Return a cached, lazily-constructed (no-arg) singleton for (base_type, name)."""
        key = (base_type, name)
        if key not in cls._singletons:
            cls._singletons[key] = cls._lookup(base_type, name)()
        return cls._singletons[key]

    @classmethod
    def names(cls, base_type: type) -> list[str]:
        return sorted(name for (b, name) in cls._classes if b is base_type)

    def __class_getitem__(cls, key: _Key):
        base_type, name = key
        return cls.get(base_type, name)


class RunRegistry:
    """Instance-only (type, name) -> object lookup scoped to one run (e.g. one
    BacktestHarness). Unlike Registry, this holds already-built instances that were
    constructed with per-run args (a Trader, a bound Strategy) rather than
    no-arg-constructible classes, and carries no process-global state -- a fresh
    RunRegistry per run means two backtests in the same process never collide or
    leak into each other, unlike Registry._singletons, which is never cleared."""

    def __init__(self):
        self._instances: dict[_Key, object] = {}

    def put(self, base_type: type, name: str, instance: object) -> None:
        self._instances[(base_type, name)] = instance

    def get(self, base_type: type, name: str):
        try:
            return self._instances[(base_type, name)]
        except KeyError as exc:
            raise KeyError(f"no {base_type.__name__} registered as {name!r}") from exc

    def __getitem__(self, key: _Key):
        base_type, name = key
        return self.get(base_type, name)
