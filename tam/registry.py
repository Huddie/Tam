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

from typing import Dict, List, Tuple, Type, TypeVar

T = TypeVar("T")
_Key = Tuple[type, str]


class Registry:
    _classes: Dict[_Key, type] = {}
    _singletons: Dict[_Key, object] = {}

    @classmethod
    def register(cls, base_type: type, name: str):
        def decorator(impl: Type[T]) -> Type[T]:
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
    def names(cls, base_type: type) -> List[str]:
        return sorted(name for (b, name) in cls._classes if b is base_type)

    def __class_getitem__(cls, key: _Key):
        base_type, name = key
        return cls.get(base_type, name)
